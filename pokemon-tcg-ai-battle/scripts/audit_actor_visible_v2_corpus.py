#!/usr/bin/env python3
"""Audit the hash-pinned telemetry corpus through the private C1 v2 boundary.

The emitted summary is deliberately aggregate-only.  Raw ``game_id`` values
are used solely to derive local episode hashes and are never emitted or stored
in an audit record.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import copy
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Mapping


_REPOSITORY_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if not _REPOSITORY_SOURCE_ROOT.is_dir():
    raise RuntimeError("audit script requires its repository-local src directory")
sys.path.insert(0, str(_REPOSITORY_SOURCE_ROOT))

from mage_ptcg.meta_specialist.actor_visible_features_v1 import CardVocabularyV1
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    OPTION_RESOLVER_TABLE_V1,
    ActorVisibleV2Error,
    build_actor_visible_decision_state_v2,
    serialize_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    atomic_write_local_dataset_v2,
    build_local_dataset_manifest_v2,
    build_local_record_v2,
    canonical_json_bytes_v2,
    iter_training_examples_v2,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import (
    RuntimeDecisionEnvelope,
    RuntimeEnvelopeError,
)


PINNED_TELEMETRY_SHA256_V2 = "de6091a5724334e431d7e3858c9bdc27b046001911ebf912b2a25c34f92e14be"
PINNED_TELEMETRY_RECORDS_V2 = 936
PINNED_CARD_DATA_SHA256_V2 = "a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373"
PINNED_CARD_DATA_UNIQUE_IDS_V2 = 1267
MAX_TELEMETRY_SNAPSHOT_BYTES_V2 = 64 * 1024 * 1024
MAX_CARD_DATA_SNAPSHOT_BYTES_V2 = 16 * 1024 * 1024
_EPISODE_DOMAIN = b"mage_ptcg:specialist-episode:v1\0"
_DEFAULT_PATH = Path(
    "/home/bfe-lab-ono/kaggle/handoff-artifacts/"
    "family-agent-activation-remediation-v1/artifacts/turn_telemetry.jsonl"
)
_DEFAULT_CARD_DATA_PATH = Path(
    "/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/data/raw/EN_Card_Data.csv"
)
_LOWER_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_POSITIVE_DECIMAL = re.compile(r"^[1-9][0-9]*$")
_RESOLUTION_KINDS = (
    "not-applicable", "actor-visible", "public-visible", "hidden-unresolved",
    "owner-resolved", "special-condition",
)


class CorpusAuditError(ValueError):
    """Raised when the pinned audit corpus or one of its hard gates is invalid."""


@dataclass(frozen=True, slots=True, init=False)
class CorpusAuditOutcomeV2:
    """Hash-sealed evidence exposed only as fresh, recursively detached copies."""

    _summary_bytes: bytes
    _record_bytes: tuple[bytes, ...]

    def __init__(
        self, *, summary: dict[str, object], records: tuple[dict[str, object], ...],
    ) -> None:
        if type(summary) is not dict or type(records) is not tuple or any(
            type(record) is not dict for record in records
        ):
            raise CorpusAuditError("audit outcome requires exact dict/tuple evidence inputs")
        object.__setattr__(self, "_summary_bytes", canonical_json_bytes_v2(summary))
        object.__setattr__(
            self, "_record_bytes", tuple(canonical_json_bytes_v2(record) for record in records),
        )

    @property
    def summary(self) -> dict[str, object]:
        """Return a fresh deep copy; caller mutation never changes sealed evidence."""
        value = json.loads(self._summary_bytes)
        if type(value) is not dict:  # pragma: no cover - constructor seals a dict.
            raise CorpusAuditError("sealed audit summary has an invalid shape")
        return value

    @property
    def records(self) -> tuple[dict[str, object], ...]:
        """Return fresh deep copies of every local-only audit record."""
        values = tuple(json.loads(raw) for raw in self._record_bytes)
        if any(type(value) is not dict for value in values):  # pragma: no cover
            raise CorpusAuditError("sealed audit records have an invalid shape")
        return values

    @property
    def summary_canonical_bytes(self) -> bytes:
        """Return the immutable canonical summary bytes used by the CLI."""
        return self._summary_bytes


@dataclass(frozen=True, slots=True)
class _VerifiedSourceSnapshotV2:
    raw_bytes: bytes
    sha256: str


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusAuditError("telemetry JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise CorpusAuditError(f"telemetry JSON contains non-finite constant {value!r}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise CorpusAuditError("telemetry JSON contains a non-finite number")
    return parsed


def _read_verified_source_snapshot_v2(
    path: Path, *, expected_sha256: str, maximum_bytes: int, source_name: str,
) -> _VerifiedSourceSnapshotV2:
    """Open once, read to exact EOF under a bound, then trust and parse these bytes."""
    if type(expected_sha256) is not str or _LOWER_HEX64.fullmatch(expected_sha256) is None:
        raise CorpusAuditError(f"expected {source_name} SHA-256 must be lowercase 64-hex")
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise CorpusAuditError(f"{source_name} snapshot byte bound is invalid")
    chunks: list[bytes] = []
    byte_count = 0
    digest = hashlib.sha256()
    descriptor = _open_regular_source_fd_v2(
        path, maximum_bytes=maximum_bytes, source_name=source_name,
    )
    handle: Any | None = None
    try:
        handle = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        with handle as entered_handle:
            while True:
                # The +1 makes concurrent growth observable without reading an
                # unbounded tail; repeated reads continue until exact EOF.
                chunk = entered_handle.read(min(1024 * 1024, maximum_bytes - byte_count + 1))
                if not chunk:
                    break
                if type(chunk) is not bytes:
                    raise CorpusAuditError(f"{source_name} snapshot reader returned non-bytes")
                byte_count += len(chunk)
                if byte_count > maximum_bytes:
                    raise CorpusAuditError(f"{source_name} snapshot exceeds its bounded byte limit")
                chunks.append(chunk)
                digest.update(chunk)
    finally:
        # ``os.fdopen`` transfers ownership when it returns, but ``with`` does
        # not call __exit__ if __enter__ raises.  Track both owners explicitly:
        # the handle owns after transfer, otherwise the raw descriptor does.
        if handle is not None:
            if not handle.closed:
                handle.close()
        elif descriptor >= 0:
            os.close(descriptor)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise CorpusAuditError(f"{source_name} SHA-256 does not match the pinned expected value")
    return _VerifiedSourceSnapshotV2(raw_bytes=b"".join(chunks), sha256=actual_sha256)


def _open_regular_source_fd_v2(
    path: Path, *, maximum_bytes: int, source_name: str,
) -> int:
    """Atomically open one non-symlink regular source without FIFO blocking."""
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise CorpusAuditError(f"{source_name} snapshot byte bound is invalid")
    required_flags: dict[str, int] = {}
    for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        value = getattr(os, name, None)
        if type(value) is not int:
            raise CorpusAuditError(f"required Linux source-open flag {name} is unavailable")
        required_flags[name] = value
    flags = os.O_RDONLY
    for value in required_flags.values():
        flags |= value
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CorpusAuditError(
            f"{source_name} must be an accessible non-symlink regular source",
        ) from exc
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise CorpusAuditError(f"{source_name} must be a non-symlink regular source")
        if status.st_size > maximum_bytes:
            raise CorpusAuditError(f"{source_name} snapshot exceeds its bounded byte limit")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _strict_int(value: object, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CorpusAuditError(f"{field} must be a non-bool integer at least {minimum}")
    return value


def _episode_id_hash(*, source_sha256: str, game_id: object) -> str:
    if type(game_id) is not str or not game_id:
        raise CorpusAuditError("telemetry game_id must be a nonempty string")
    return hashlib.sha256(
        _EPISODE_DOMAIN + canonical_json_bytes_v2({
            "source_sha256": source_sha256, "game_id": game_id,
        })
    ).hexdigest()


def _exact_card_data_vocabulary(
    snapshot: _VerifiedSourceSnapshotV2,
) -> CardVocabularyV1:
    """Parse ``Card ID`` only from already hash-verified immutable bytes."""
    identifiers: set[int] = set()
    try:
        with io.StringIO(snapshot.raw_bytes.decode("utf-8"), newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or reader.fieldnames.count("Card ID") != 1:
                raise CorpusAuditError("card-data CSV is missing the exact Card ID column")
            for row_number, row in enumerate(reader, start=2):
                raw_id = row.get("Card ID")
                if type(raw_id) is not str or _POSITIVE_DECIMAL.fullmatch(raw_id) is None:
                    raise CorpusAuditError(f"card-data Card ID is malformed at CSV row {row_number}")
                card_id = int(raw_id)
                identifiers.add(card_id)
    except UnicodeDecodeError as exc:
        raise CorpusAuditError("card-data CSV is not UTF-8 text") from exc
    if not identifiers:
        raise CorpusAuditError("card-data Card ID column is empty")
    if snapshot.sha256 == PINNED_CARD_DATA_SHA256_V2 and len(identifiers) != PINNED_CARD_DATA_UNIQUE_IDS_V2:
        raise CorpusAuditError("pinned card-data unique Card ID count differs from the frozen value")
    return CardVocabularyV1(
        recognized_card_ids=frozenset(identifiers), source_sha256=snapshot.sha256,
        environment_version="pinned-en-card-data-audit-cabt-1.32.0",
        usage_decision="unqualified", test_only=False, permission_decision="unqualified",
    )


def _validate_injected_audit_vocabulary(vocabulary: object) -> CardVocabularyV1:
    """Accept only an already-constructed, locally explicit fixture vocabulary."""
    if not isinstance(vocabulary, CardVocabularyV1):
        raise CorpusAuditError("injected audit vocabulary must be CardVocabularyV1")
    CardVocabularyV1.__post_init__(vocabulary)
    return vocabulary


def _assert_all_actor_visible_cards_are_known(
    state: object, vocabulary: CardVocabularyV1,
) -> None:
    """Audit input is closed over the pinned card database; UNK is not admitted."""
    payload = serialize_actor_visible_decision_state_v2(state)  # type: ignore[arg-type]
    unknown: set[int] = set()

    def walk(value: object) -> None:
        if type(value) is dict:
            for key, child in value.items():
                if key == "card_id" and type(child) is int and child > 0:
                    if child not in vocabulary.recognized_card_ids:
                        unknown.add(child)
                walk(child)
        elif type(value) is list:
            for child in value:
                walk(child)

    walk(payload)
    if unknown:
        raise CorpusAuditError("actor-visible state refers to card IDs absent from the audit vocabulary")


def _counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter, key=lambda item: str(item))}


def _hard_gate(summary: Mapping[str, object]) -> None:
    """Apply the fixed observed-corpus assertions only after all records are read."""
    expected_shapes: dict[str, object] = {
        "first_player": {"-1": 12, "0": 924},
        "stadium_length": {"0": 378, "1": 558},
        "deck_reveal_nonnull": 86,
        "looking_nonnull": 13,
        "context_card_nonnull": 49,
        "effect_nonnull": 183,
        "remain_damage_counter": {"0": 936},
        "remain_energy_cost": {"0": 916, "1": 19, "2": 1},
        "legal_option_tail": {"61": 1, "64": 1, "67": 1},
        "runtime_tail_valid": {"61": 1, "64": 1, "67": 1},
        "max_legal_options": 67,
        "ability_stadium_candidates": 31,
        "card_vocabulary": {
            "source_sha256": PINNED_CARD_DATA_SHA256_V2,
            "recognized_card_ids": PINNED_CARD_DATA_UNIQUE_IDS_V2,
        },
    }
    expected_top = {
        "records": 936, "c1_valid": 936, "local_records_valid": 936,
        "default_training_examples": 0, "validation_errors": {},
        "public_identity": {"representable": 339, "duplicate-public-identity": 597},
    }
    for key, expected in {**expected_top, **expected_shapes}.items():
        if summary.get(key) != expected:
            raise CorpusAuditError(f"pinned hard gate {key} differs from the frozen observed value")
    maximum_collection = summary.get("max_visible_card_collection")
    if type(maximum_collection) is not int or maximum_collection > 60:
        raise CorpusAuditError("pinned hard gate found a card collection above the 60-card bound")


def _read_nonblank_records(snapshot: _VerifiedSourceSnapshotV2) -> list[dict[str, object]]:
    """Parse telemetry only after its exact immutable byte snapshot is trusted."""
    rows: list[dict[str, object]] = []
    try:
        text = snapshot.raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorpusAuditError("telemetry snapshot is not UTF-8 text") from exc
    with io.StringIO(text, newline=None) as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(
                    line, object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_nonfinite, parse_float=_parse_finite_float,
                )
            except (json.JSONDecodeError, CorpusAuditError) as exc:
                raise CorpusAuditError(f"telemetry line {line_number} is invalid") from exc
            if type(parsed) is not dict:
                raise CorpusAuditError(f"telemetry line {line_number} must be a JSON object")
            rows.append(parsed)
    return rows


def _source_for_audit(source_sha256: str) -> dict[str, object]:
    return {
        "kind": "pinned-telemetry-audit", "artifact_sha256": source_sha256,
        "synthetic": True, "synthetic_fields": ["step"], "training_eligible": False,
        "usage_class": "audit_only_unqualified", "permission_manifest_id": None,
    }


def _max_visible_card_collection(state: object) -> int:
    """Return the maximum C1v2 collection size; every card collection caps at 60."""
    view = state.information_view  # type: ignore[union-attr]
    selection = view.private_state.selection_view
    lengths = [
        len(view.private_state.own_hand), len(selection.deck_reveal or ()),
        len(selection.looking or ()), len(view.self_player.discard),
        len(view.opponent_player.discard), len(view.self_player.active),
        len(view.self_player.bench), len(view.opponent_player.active), len(view.opponent_player.bench),
    ]
    for player in (view.self_player, view.opponent_player):
        for pokemon in (*player.active, *player.bench):
            if pokemon is not None:
                lengths.extend((
                    len(pokemon.energies), len(pokemon.energy_cards), len(pokemon.tools),
                    len(pokemon.pre_evolution),
                ))
    return max(lengths, default=0)


def audit_telemetry_corpus_v2(
    path: str | Path, *, expected_sha256: str = PINNED_TELEMETRY_SHA256_V2,
    expected_nonblank_records: int | None = None, enforce_pinned_hard_gates: bool = True,
    card_data_path: str | Path | None = None,
    expected_card_data_sha256: str = PINNED_CARD_DATA_SHA256_V2,
    vocabulary: CardVocabularyV1 | None = None,
) -> CorpusAuditOutcomeV2:
    """Run C1 v2 plus Task 5 audit conversion over one SHA-pinned JSONL corpus."""
    corpus = Path(path)
    if enforce_pinned_hard_gates and expected_sha256 != PINNED_TELEMETRY_SHA256_V2:
        raise CorpusAuditError("pinned hard gates require the pinned telemetry SHA-256")
    telemetry_snapshot = _read_verified_source_snapshot_v2(
        corpus, expected_sha256=expected_sha256,
        maximum_bytes=MAX_TELEMETRY_SNAPSHOT_BYTES_V2, source_name="telemetry",
    )
    actual_sha256 = telemetry_snapshot.sha256
    rows = _read_nonblank_records(telemetry_snapshot)
    expected_count = PINNED_TELEMETRY_RECORDS_V2 if expected_nonblank_records is None else expected_nonblank_records
    if type(expected_count) is not int or expected_count < 0 or len(rows) != expected_count:
        raise CorpusAuditError("telemetry nonblank record count does not match the required gate")
    if vocabulary is not None and card_data_path is not None:
        raise CorpusAuditError("supply either an injected vocabulary or card-data path, never both")
    if vocabulary is not None:
        if enforce_pinned_hard_gates:
            raise CorpusAuditError("pinned hard gates require the pinned card-data CSV vocabulary")
        audit_vocabulary = _validate_injected_audit_vocabulary(vocabulary)
    else:
        if enforce_pinned_hard_gates and expected_card_data_sha256 != PINNED_CARD_DATA_SHA256_V2:
            raise CorpusAuditError("pinned hard gates require the pinned card-data SHA-256")
        card_data_snapshot = _read_verified_source_snapshot_v2(
            _DEFAULT_CARD_DATA_PATH if card_data_path is None else Path(card_data_path),
            expected_sha256=expected_card_data_sha256,
            maximum_bytes=MAX_CARD_DATA_SNAPSHOT_BYTES_V2, source_name="card-data",
        )
        audit_vocabulary = _exact_card_data_vocabulary(card_data_snapshot)
    decision_indexes: defaultdict[str, int] = defaultdict(int)
    records: list[dict[str, object]] = []
    errors: Counter[str] = Counter()
    selection_schemas: Counter[tuple[int, int]] = Counter()
    option_variants: Counter[int] = Counter()
    option_areas: Counter[tuple[int, str]] = Counter()
    resolver_kinds: Counter[str] = Counter()
    public_identity: Counter[str] = Counter()
    first_player: Counter[int] = Counter()
    stadium_length: Counter[int] = Counter()
    remain_damage_counter: Counter[int] = Counter()
    remain_energy_cost: Counter[int] = Counter()
    legal_option_tail: Counter[int] = Counter()
    runtime_tail_valid: Counter[int] = Counter()
    deck_reveal_nonnull = looking_nonnull = context_card_nonnull = effect_nonnull = 0
    ability_stadium_candidates = 0
    max_legal_options = 0
    max_visible_card_collection = 0
    c1_valid = 0

    for ordinal, row in enumerate(rows):
        try:
            game_id = row.get("game_id")
            episode_id_hash = _episode_id_hash(source_sha256=actual_sha256, game_id=game_id)
            decision_index = decision_indexes[episode_id_hash]
            decision_indexes[episode_id_hash] += 1
            observation = row.get("public_observation")
            if type(observation) is not dict:
                raise CorpusAuditError("telemetry record has no object public_observation")
            # The archive lacks the true outer step.  A fixed zero is the only
            # synthetic value, and it cannot encode an ordinal or availability.
            audit_observation = copy.deepcopy(observation)
            audit_observation["step"] = 0
            state = build_actor_visible_decision_state_v2(audit_observation)
        except (ActorVisibleV2Error, CorpusAuditError, IndexError, KeyError, TypeError, ValueError) as exc:
            errors[f"c1:{type(exc).__name__}"] += 1
            continue

        c1_valid += 1
        try:
            _assert_all_actor_visible_cards_are_known(state, audit_vocabulary)
            view = state.information_view
            selected_indices = row.get("selected_action")
            if type(selected_indices) is not list:
                raise CorpusAuditError("telemetry selected_action must be a list")
            selected_ids: list[str] = []
            for index in selected_indices:
                selected_ids.append(state.legal_actions[
                    _strict_int(index, field="selected_action[]")
                ].local_action_id)
            if not is_ordered_selection(view.selection_type, view.selection_context):
                selected_ids.sort()
            if len(selected_ids) != len(set(selected_ids)):
                raise CorpusAuditError("telemetry selected_action maps to duplicate local IDs")
            record = build_local_record_v2(
                state=state, vocabulary=audit_vocabulary, episode_id_hash=episode_id_hash,
                decision_index=decision_index, selection=tuple(selected_ids),
                behavior={"status": "action_only", "selection": selected_ids},
                teacher={"status": "unavailable", "reason": "telemetry has no complete-action ranking"},
                student={"status": "fallback", "selection": [], "scores": [], "reason": "telemetry has no student decode"},
                source=_source_for_audit(actual_sha256),
                provenance={"source_record_ordinal": ordinal},
            )
        except (ActorVisibleV2Error, CorpusAuditError, LocalDatasetV2Error, IndexError, KeyError, TypeError, ValueError) as exc:
            errors[f"local_record:{type(exc).__name__}"] += 1
            continue

        records.append(record)
        try:
            selection_schemas[(view.selection_type, view.selection_context)] += 1
            first_player[view.first_player] += 1
            stadium_length[int(view.board_stadium is not None)] += 1
            selection_view = view.private_state.selection_view
            deck_reveal_nonnull += int(selection_view.deck_reveal is not None)
            looking_nonnull += int(selection_view.looking is not None)
            context_card_nonnull += int(selection_view.context_card is not None)
            effect_nonnull += int(selection_view.effect is not None)
            remain_damage_counter[view.remain_damage_counter] += 1
            remain_energy_cost[view.remain_energy_cost] += 1
            max_legal_options = max(max_legal_options, len(state.legal_actions))
            max_visible_card_collection = max(max_visible_card_collection, _max_visible_card_collection(state))
            if len(state.legal_actions) > 60:
                legal_option_tail[len(state.legal_actions)] += 1
                RuntimeDecisionEnvelope.from_actor_visible_state(state, vocabulary=audit_vocabulary)
                runtime_tail_valid[len(state.legal_actions)] += 1
            for option, action in zip(audit_observation["select"]["option"], state.legal_actions, strict=True):
                if type(option) is not dict:
                    raise CorpusAuditError("C1 accepted a non-object option")
                option_type = action.action_key.option_type
                option_variants[option_type] += 1
                option_areas[(option_type, str(option.get("area", "not-applicable")))] += 1
                for endpoint_name, endpoint in (
                    ("source", action.binding.core.source),
                    ("target", action.binding.core.target),
                    ("host", action.binding.core.host),
                ):
                    resolver_kinds[f"{endpoint_name}:{endpoint.resolution_kind}"] += 1
                ability_stadium_candidates += int(
                    option_type == 10 and action.binding.core.source.semantic_zone == "stadium"
                )
            public_identity[record["public_audit"]["projection_status"]] += 1
        except (RuntimeEnvelopeError, CorpusAuditError, IndexError, KeyError, TypeError, ValueError) as exc:
            errors[f"audit_metrics:{type(exc).__name__}"] += 1

    default_training_examples = 0
    if not errors:
        with tempfile.TemporaryDirectory(prefix="actor-visible-v2-audit-") as temporary:
            dataset_path = Path(temporary) / "audit.local.jsonl"
            manifest = build_local_dataset_manifest_v2(
                records=tuple(records), environment_version="pinned-telemetry-audit-cabt-1.32.0",
                deck_fingerprint=actual_sha256, trusted_permissions={},
            )
            atomic_write_local_dataset_v2(dataset_path, records=tuple(records), manifest=manifest)
            default_training_examples = sum(1 for _ in iter_training_examples_v2(
                dataset_path, manifest=manifest, vocabulary=audit_vocabulary, trusted_permissions={},
                qualification_time_utc="1970-01-01T00:00:00Z",
            ))

    report: dict[str, object] = {
        "source_sha256": actual_sha256, "records": len(rows), "c1_valid": c1_valid,
        "local_records_valid": len(records), "default_training_examples": default_training_examples,
        "validation_errors": _counter_dict(errors),
        "public_identity": _counter_dict(public_identity),
        "selection_schemas": _counter_dict(selection_schemas),
        "option_variants": _counter_dict(option_variants), "option_areas": _counter_dict(option_areas),
        "resolver_kinds": _counter_dict(resolver_kinds),
        "zero_resolver_kinds": [
            kind for kind in _RESOLUTION_KINDS
            if not any(key.endswith(f":{kind}") for key in resolver_kinds)
        ],
        "zero_option_resolver_branches": [
            f"{row.option_type}:{row.operation}:{row.source_resolver}"
            for row in OPTION_RESOLVER_TABLE_V1.values()
            if option_variants[row.option_type] == 0
        ],
        "first_player": _counter_dict(first_player), "stadium_length": _counter_dict(stadium_length),
        "deck_reveal_nonnull": deck_reveal_nonnull, "looking_nonnull": looking_nonnull,
        "context_card_nonnull": context_card_nonnull, "effect_nonnull": effect_nonnull,
        "remain_damage_counter": _counter_dict(remain_damage_counter),
        "remain_energy_cost": _counter_dict(remain_energy_cost),
        "legal_option_tail": _counter_dict(legal_option_tail),
        "runtime_tail_valid": _counter_dict(runtime_tail_valid),
        "max_legal_options": max_legal_options,
        "max_visible_card_collection": max_visible_card_collection,
        "ability_stadium_candidates": ability_stadium_candidates,
        "card_vocabulary": {
            "source_sha256": audit_vocabulary.source_sha256,
            "recognized_card_ids": len(audit_vocabulary.recognized_card_ids),
        },
        "episode_count": len(decision_indexes),
    }
    if enforce_pinned_hard_gates:
        _hard_gate(report)
    return CorpusAuditOutcomeV2(summary=report, records=tuple(records))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=_DEFAULT_PATH)
    parser.add_argument("--card-data", type=Path, default=_DEFAULT_CARD_DATA_PATH)
    args = parser.parse_args()
    outcome = audit_telemetry_corpus_v2(args.corpus, card_data_path=args.card_data)
    print(outcome.summary_canonical_bytes.decode("utf-8"))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the public function/CLI.
    raise SystemExit(main())
