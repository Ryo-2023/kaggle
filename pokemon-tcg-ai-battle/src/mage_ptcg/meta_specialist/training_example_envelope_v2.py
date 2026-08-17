"""Frozen, serial-free L1A training envelopes from one sealed dataset snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
import weakref

from mage_ptcg.exact_file import ExactFileSnapshotError, read_exact_regular_file
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardBagV1,
    CardVocabularyV1,
    PokemonEntityV1,
    SemanticActionV1,
    SemanticEndpointV1,
    SpecialistFeatureError,
    SpecialistModelInputV1,
    validate_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    MAX_LOCAL_RECORD_BYTES_V2,
    TrustedPermissionV1,
    _RECORD_KEYS,
    _exact_mapping,
    _qualified_for_training,
    _utc,
    _validate_manifest,
    _validate_source,
    _verify_manifest_trust,
    canonical_json_bytes_v2,
    parse_canonical_json_bytes_v2,
    semantic_loss_rows_from_record_v2,
    validate_local_record_v2,
)


# These bound this repository's *own* sealed teacher corpora -- files this package
# wrote, in the run directory it wrote them to -- not untrusted third-party input.
# At 512 MiB the bound contradicted the job: a 300-game teacher corpus is roughly
# 0.5-3 MB per game depending on how many candidate actions the archetype offers,
# so the two verbose lanes measured here came out at 525 MB (archaludon) and
# 877 MB (alakazam) and could not be sealed at all, while the two terse lanes fit
# with ~15 MB to spare.  A ceiling that admits an archetype only if its decisions
# are short is not a safety property, it is an accidental archetype filter.
#
# 4 GiB is chosen against the machine the corpora are sealed on rather than as a
# round number: sealing holds the parsed corpus in memory, the largest measured
# corpus is 877 MB of JSONL, and the guard tests monkeypatch these constants to 1
# rather than relying on their value, so raising them does not weaken the checks.
MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2 = 4 * 1024 * 1024 * 1024
MAX_TRAINING_ENVELOPE_SPOOL_BYTES_V2 = 4 * 1024 * 1024 * 1024
_ENVELOPE_KEYS = frozenset({
    "model_input", "loss_rows", "value_target", "record_id", "episode_id_hash",
    "near_duplicate_id", "record_content_hash", "source_kind", "source_artifact_sha256",
    "permission_manifest_id", "permission_content_hash", "permission_trusted_bytes_sha256",
    "manifest_id", "manifest_content_hash", "dataset_snapshot_sha256",
    "example_quality_weight",
})
_FORBIDDEN_KEYS = frozenset({
    "record", "game_id", "path", "local_action_id", "action_key_digest",
    "action_key_payload", "actor_binding", "serial", "index",
})
_HEX64 = frozenset("0123456789abcdef")
_MODEL_INPUT_KEYS = frozenset({
    "schema_version", "feature_domain", "feature_schema_hash", "state_scalars",
    "single_card_ids", "card_bags", "pokemon_entities", "candidate_rows",
})
_POKEMON_KEYS = frozenset({
    "owner_role", "zone", "card_id", "hp", "max_hp", "appear_this_turn",
    "energy_type_counts", "energy_cards", "tools", "pre_evolution",
})
_ENDPOINT_KEYS = frozenset({
    "visibility", "owner_role", "semantic_zone", "card_id", "host_card_id", "pokemon",
})
_SEMANTIC_KEYS = frozenset({
    "selection_type", "selection_context", "option_type", "operation", "source", "target",
    "host", "number", "attack_id", "special_condition", "energy_count", "skill_card_id",
})
_LOSS_ROW_KEYS = frozenset({"semantic_prefix", "token_masses", "reach_mass"})


def _sealed_manifest(value: object) -> dict[str, Any]:
    """Detach caller-owned manifest data before trust and identity verification."""
    parsed = parse_canonical_json_bytes_v2(canonical_json_bytes_v2(value))
    return _validate_manifest(parsed)


def _reject_forbidden_keys(value: object) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is dict:
            if set(current) & _FORBIDDEN_KEYS:
                raise LocalDatasetV2Error("training envelope contains a forbidden private field")
            pending.extend(current.values())
        elif type(current) is list:
            pending.extend(current)


def reject_forbidden_private_fields_v2(value: object) -> None:
    """Fail closed if any nested mapping carries a private binding field.

    Exposed so downstream publishers re-check the same closed set instead of
    restating it, keeping one definition of "private" across L1.
    """
    _reject_forbidden_keys(value)


def _exact_dict(value: object, *, field: str, keys: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise LocalDatasetV2Error(f"{field} has the wrong closed field set")
    return value


def _exact_int_list(value: object, *, field: str, maximum: int = 65_536) -> tuple[int, ...]:
    if type(value) is not list or len(value) > maximum or any(type(item) is not int for item in value):
        raise LocalDatasetV2Error(f"{field} must be a bounded exact integer list")
    return tuple(value)


def _pokemon_from_payload(value: object, *, field: str) -> PokemonEntityV1:
    payload = _exact_dict(value, field=field, keys=_POKEMON_KEYS)
    try:
        return PokemonEntityV1(
            owner_role=payload["owner_role"], zone=payload["zone"], card_id=payload["card_id"],
            hp=payload["hp"], max_hp=payload["max_hp"],
            appear_this_turn=payload["appear_this_turn"],
            energy_type_counts=_exact_int_list(payload["energy_type_counts"], field=f"{field}.energy_type_counts"),
            energy_cards=_exact_int_list(payload["energy_cards"], field=f"{field}.energy_cards"),
            tools=_exact_int_list(payload["tools"], field=f"{field}.tools"),
            pre_evolution=_exact_int_list(payload["pre_evolution"], field=f"{field}.pre_evolution"),
        )
    except (SpecialistFeatureError, TypeError, ValueError) as exc:
        raise LocalDatasetV2Error(f"{field} is not a closed Pokemon feature") from exc


def _endpoint_from_payload(value: object, *, field: str) -> SemanticEndpointV1:
    payload = _exact_dict(value, field=field, keys=_ENDPOINT_KEYS)
    pokemon = None if payload["pokemon"] is None else _pokemon_from_payload(
        payload["pokemon"], field=f"{field}.pokemon",
    )
    try:
        return SemanticEndpointV1(
            visibility=payload["visibility"], owner_role=payload["owner_role"],
            semantic_zone=payload["semantic_zone"], card_id=payload["card_id"],
            host_card_id=payload["host_card_id"], pokemon=pokemon,
        )
    except (SpecialistFeatureError, TypeError, ValueError) as exc:
        raise LocalDatasetV2Error(f"{field} is not a closed semantic endpoint") from exc


def _semantic_from_payload(value: object, *, field: str) -> SemanticActionV1:
    payload = _exact_dict(value, field=field, keys=_SEMANTIC_KEYS)
    try:
        return SemanticActionV1(
            selection_type=payload["selection_type"], selection_context=payload["selection_context"],
            option_type=payload["option_type"], operation=payload["operation"],
            source=_endpoint_from_payload(payload["source"], field=f"{field}.source"),
            target=_endpoint_from_payload(payload["target"], field=f"{field}.target"),
            host=_endpoint_from_payload(payload["host"], field=f"{field}.host"),
            number=payload["number"], attack_id=payload["attack_id"],
            special_condition=payload["special_condition"], energy_count=payload["energy_count"],
            skill_card_id=payload["skill_card_id"],
        )
    except (SpecialistFeatureError, TypeError, ValueError) as exc:
        raise LocalDatasetV2Error(f"{field} is not a closed semantic action") from exc


def _model_input_from_payload(value: object) -> SpecialistModelInputV1:
    payload = _exact_dict(value, field="training envelope model_input", keys=_MODEL_INPUT_KEYS)
    single_cards = _exact_dict(
        payload["single_card_ids"], field="model_input.single_card_ids",
        keys=frozenset({"stadium", "context", "effect"}),
    )
    bags_payload = _exact_dict(
        payload["card_bags"], field="model_input.card_bags",
        keys=frozenset({"own_hand", "deck_reveal", "looking_visible", "self_discard", "opponent_discard"}),
    )
    bags: dict[str, CardBagV1] = {}
    for name, raw_bag in bags_payload.items():
        bag = _exact_dict(raw_bag, field=f"model_input.card_bags.{name}", keys=frozenset({"tokens", "mask"}))
        bags[name] = CardBagV1(
            tokens=_exact_int_list(bag["tokens"], field=f"model_input.card_bags.{name}.tokens"),
            mask=_exact_int_list(bag["mask"], field=f"model_input.card_bags.{name}.mask"),
        )
    if type(payload["pokemon_entities"]) is not list or type(payload["candidate_rows"]) is not list:
        raise LocalDatasetV2Error("model_input entity/candidate rows must be exact lists")
    try:
        model_input = SpecialistModelInputV1(
            schema_version=payload["schema_version"], feature_domain=payload["feature_domain"],
            feature_schema_hash=payload["feature_schema_hash"],
            state_scalars=_exact_int_list(payload["state_scalars"], field="model_input.state_scalars"),
            single_card_ids=single_cards, card_bags=bags,
            pokemon_entities=tuple(
                _pokemon_from_payload(item, field=f"model_input.pokemon_entities[{index}]")
                for index, item in enumerate(payload["pokemon_entities"])
            ),
            candidate_rows=tuple(
                _semantic_from_payload(item, field=f"model_input.candidate_rows[{index}]")
                for index, item in enumerate(payload["candidate_rows"])
            ),
        )
        validate_specialist_model_input_v1(model_input)
    except (SpecialistFeatureError, TypeError, ValueError) as exc:
        raise LocalDatasetV2Error("training envelope model_input is invalid") from exc
    if model_input.to_dict() != payload:
        raise LocalDatasetV2Error("training envelope model_input is not canonical")
    return model_input


def _validate_model_input_payload(value: object) -> dict[str, object]:
    return _model_input_from_payload(value).to_dict()


def specialist_model_input_from_training_payload_v2(value: object) -> SpecialistModelInputV1:
    """Rebuild the live, validated :class:`SpecialistModelInputV1` for one training
    example's ``model_input`` payload.

    Thin public wrapper around the same private parser
    :func:`_validate_model_input_payload` uses internally, exposed because a model
    adapter (``neural_adapter_v1.py``) needs the live dataclass object -- not just
    its canonical dict -- once per training example, to call
    :meth:`SpecialistPolicyModelV1.step_logits` for each of that example's rows.
    """
    return _model_input_from_payload(value)


def semantic_action_from_training_payload_v2(value: object, *, field: str = "semantic_action") -> SemanticActionV1:
    """Rebuild one closed :class:`SemanticActionV1` from a canonical training payload dict.

    Thin public wrapper around the same private parser :func:`_validate_loss_rows`
    uses internally for ``semantic_prefix`` entries and ``token_masses`` semantic
    tokens, exposed so a model adapter can rebuild the identical
    :class:`SemanticActionV1` objects without duplicating this parsing.
    """
    return _semantic_from_payload(value, field=field)


def _validate_loss_rows(value: object, *, model_input: Mapping[str, object]) -> list[dict[str, object]]:
    if type(value) is not list or not value or len(value) > 65_536:
        raise LocalDatasetV2Error("training envelope loss_rows must be a bounded nonempty exact list")
    candidate_keys = {
        canonical_json_bytes_v2(item) for item in model_input["candidate_rows"]  # type: ignore[index]
    }
    prior_prefix: bytes | None = None
    output: list[dict[str, object]] = []
    for row_index, raw_row in enumerate(value):
        row = _exact_dict(raw_row, field=f"loss_rows[{row_index}]", keys=_LOSS_ROW_KEYS)
        prefix = row["semantic_prefix"]
        tokens = row["token_masses"]
        reach = row["reach_mass"]
        if type(prefix) is not list or len(prefix) > 64 or type(tokens) is not list or not tokens:
            raise LocalDatasetV2Error("loss row prefix/domain has an invalid bounded shape")
        for offset, semantic in enumerate(prefix):
            checked = _semantic_from_payload(semantic, field=f"loss_rows[{row_index}].semantic_prefix[{offset}]")
            if canonical_json_bytes_v2(checked.to_dict()) not in candidate_keys:
                raise LocalDatasetV2Error("loss row prefix is outside the model candidate domain")
        prefix_key = canonical_json_bytes_v2(prefix)
        if prior_prefix is not None and prefix_key <= prior_prefix:
            raise LocalDatasetV2Error("loss row prefixes must be sorted and unique")
        prior_prefix = prefix_key
        if type(reach) is not float or not math.isfinite(reach) or not 0.0 < reach <= 1.0:
            raise LocalDatasetV2Error("loss row reach_mass must be a finite float in (0,1]")
        prior_semantic: bytes | None = None
        stop_seen = False
        masses: list[float] = []
        semantic_count = 0
        for token_index, raw_token in enumerate(tokens):
            if type(raw_token) is not dict or raw_token.get("kind") not in {"semantic", "stop"}:
                raise LocalDatasetV2Error("loss row token has an invalid closed kind")
            if raw_token["kind"] == "semantic":
                token = _exact_dict(
                    raw_token, field=f"loss_rows[{row_index}].token_masses[{token_index}]",
                    keys=frozenset({"kind", "semantic_action", "mass"}),
                )
                if stop_seen:
                    raise LocalDatasetV2Error("loss row STOP must follow all semantic tokens")
                semantic = _semantic_from_payload(token["semantic_action"], field="loss row semantic token")
                semantic_key = canonical_json_bytes_v2(semantic.to_dict())
                if semantic_key not in candidate_keys or (prior_semantic is not None and semantic_key <= prior_semantic):
                    raise LocalDatasetV2Error("loss row semantic token domain must be sorted, unique, and model-bound")
                prior_semantic = semantic_key
                semantic_count += 1
            else:
                token = _exact_dict(
                    raw_token, field=f"loss_rows[{row_index}].token_masses[{token_index}]",
                    keys=frozenset({"kind", "mass"}),
                )
                if stop_seen:
                    raise LocalDatasetV2Error("loss row may contain STOP at most once")
                stop_seen = True
            mass = token["mass"]
            if type(mass) is not float or not math.isfinite(mass) or not 0.0 <= mass <= 1.0:
                raise LocalDatasetV2Error("loss row masses must be finite floats in [0,1]")
            masses.append(mass)
        if semantic_count == 0:
            raise LocalDatasetV2Error("forced sole STOP must not create a loss row")
        if not math.isclose(math.fsum(masses), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
            raise LocalDatasetV2Error("loss row target masses must sum to one")
        output.append(row)
    return output


def _validate_envelope_payload(value: object) -> dict[str, object]:
    payload = _exact_dict(value, field="training envelope", keys=_ENVELOPE_KEYS)
    _reject_forbidden_keys(payload)
    model_input = _validate_model_input_payload(payload["model_input"])
    _validate_loss_rows(payload["loss_rows"], model_input=model_input)
    quality = payload["example_quality_weight"]
    if type(quality) is not float or not math.isfinite(quality) or not 0.0 < quality <= 1.0:
        raise LocalDatasetV2Error("example_quality_weight must be a finite float in (0,1]")
    target = payload["value_target"]
    if target is not None and (
        type(target) is not float or not math.isfinite(target) or not -1.0 <= target <= 1.0
    ):
        raise LocalDatasetV2Error("value_target must be null or a finite float in [-1,1]")
    for field in (
        "record_id", "episode_id_hash", "near_duplicate_id", "record_content_hash",
        "source_artifact_sha256", "permission_manifest_id", "permission_content_hash",
        "permission_trusted_bytes_sha256", "manifest_id", "manifest_content_hash",
        "dataset_snapshot_sha256",
    ):
        item = payload[field]
        if type(item) is not str or len(item) != 64 or any(character not in _HEX64 for character in item):
            raise LocalDatasetV2Error(f"training envelope {field} must be lowercase 64-hex")
    if type(payload["source_kind"]) is not str or not payload["source_kind"] or len(payload["source_kind"]) > 256:
        raise LocalDatasetV2Error("training envelope source_kind must be a bounded nonempty string")
    return payload


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class TrainingExampleEnvelopeV2:
    """Canonical sealed L1A payload; accessors always return detached copies."""

    _payload_bytes: bytes

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("TrainingExampleEnvelopeV2 objects are issued only by the verified loader")

    def __copy__(self) -> "TrainingExampleEnvelopeV2":
        return _restore_unissued_envelope_v2(self._payload_bytes)

    def __deepcopy__(self, _memo: dict[int, object]) -> "TrainingExampleEnvelopeV2":
        return _restore_unissued_envelope_v2(bytes(self._payload_bytes))

    def __reduce_ex__(self, _protocol: int):
        return (_restore_unissued_envelope_v2, (self._payload_bytes,))

    def to_dict(self) -> dict[str, object]:
        require_training_example_envelope_v2(self)
        value = parse_canonical_json_bytes_v2(self._payload_bytes)
        return _validate_envelope_payload(value)

    @property
    def model_input(self) -> dict[str, object]:
        return self.to_dict()["model_input"]  # type: ignore[return-value]

    @property
    def loss_rows(self) -> list[dict[str, object]]:
        return self.to_dict()["loss_rows"]  # type: ignore[return-value]

    @property
    def value_target(self) -> float | None:
        return self.to_dict()["value_target"]  # type: ignore[return-value]

    @property
    def example_quality_weight(self) -> float:
        return self.to_dict()["example_quality_weight"]  # type: ignore[return-value]

    def _hash_field(self, name: str) -> str:
        value = self.to_dict()[name]
        if type(value) is not str:  # pragma: no cover - sealed constructor.
            raise LocalDatasetV2Error("sealed training envelope has an invalid hash field")
        return value

    @property
    def record_id(self) -> str:
        return self._hash_field("record_id")

    @property
    def episode_id_hash(self) -> str:
        return self._hash_field("episode_id_hash")

    @property
    def near_duplicate_id(self) -> str:
        return self._hash_field("near_duplicate_id")

    @property
    def record_content_hash(self) -> str:
        return self._hash_field("record_content_hash")

    @property
    def source_kind(self) -> str:
        return self._hash_field("source_kind")

    @property
    def source_artifact_sha256(self) -> str:
        return self._hash_field("source_artifact_sha256")

    @property
    def permission_manifest_id(self) -> str:
        return self._hash_field("permission_manifest_id")

    @property
    def permission_content_hash(self) -> str:
        return self._hash_field("permission_content_hash")

    @property
    def permission_trusted_bytes_sha256(self) -> str:
        return self._hash_field("permission_trusted_bytes_sha256")

    @property
    def manifest_id(self) -> str:
        return self._hash_field("manifest_id")

    @property
    def manifest_content_hash(self) -> str:
        return self._hash_field("manifest_content_hash")

    @property
    def dataset_snapshot_sha256(self) -> str:
        return self._hash_field("dataset_snapshot_sha256")

    def training_example(self) -> dict[str, object]:
        """Return the legacy three-key consumer projection as detached JSON data."""
        payload = self.to_dict()
        quality = payload["example_quality_weight"]
        return {
            "model_input": payload["model_input"],
            "loss_rows": [{**row, "quality_weight": quality} for row in payload["loss_rows"]],
            "value_target": payload["value_target"],
        }


_ISSUED_ENVELOPES_V2: weakref.WeakKeyDictionary[TrainingExampleEnvelopeV2, bytes] = weakref.WeakKeyDictionary()
_ENVELOPE_FINGERPRINT_DOMAIN_V2 = b"mage_ptcg:training-example-envelope-issued:v2\0"


def _restore_unissued_envelope_v2(payload_bytes: bytes) -> TrainingExampleEnvelopeV2:
    value = object.__new__(TrainingExampleEnvelopeV2)
    object.__setattr__(value, "_payload_bytes", payload_bytes)
    return value


def _payload_fingerprint_v2(payload_bytes: bytes) -> bytes:
    return hashlib.sha256(_ENVELOPE_FINGERPRINT_DOMAIN_V2 + payload_bytes).digest()


def _seal_and_issue_envelope_v2(payload: Mapping[str, object]) -> TrainingExampleEnvelopeV2:
    payload_bytes = _seal_envelope_payload_v2(payload)
    return _issue_envelope_bytes_v2(payload_bytes)


def _seal_envelope_payload_v2(payload: Mapping[str, object]) -> bytes:
    normalized = parse_canonical_json_bytes_v2(canonical_json_bytes_v2(payload))
    checked = _validate_envelope_payload(normalized)
    return canonical_json_bytes_v2(checked)


def _issue_envelope_bytes_v2(payload_bytes: bytes) -> TrainingExampleEnvelopeV2:
    normalized = parse_canonical_json_bytes_v2(payload_bytes)
    checked = _validate_envelope_payload(normalized)
    if canonical_json_bytes_v2(checked) != payload_bytes:
        raise LocalDatasetV2Error("sealed training envelope bytes are not canonical")
    value = _restore_unissued_envelope_v2(payload_bytes)
    _ISSUED_ENVELOPES_V2[value] = _payload_fingerprint_v2(payload_bytes)
    return value


def require_training_example_envelope_v2(value: object) -> TrainingExampleEnvelopeV2:
    """Require this exact live object to have been issued by the verified loader."""
    if type(value) is not TrainingExampleEnvelopeV2:
        raise LocalDatasetV2Error("training envelope must be an exact issued capability object")
    try:
        payload_bytes = value._payload_bytes
        expected = _ISSUED_ENVELOPES_V2.get(value)
    except (AttributeError, TypeError) as exc:
        raise LocalDatasetV2Error("training envelope is not an issued capability object") from exc
    if (
        type(payload_bytes) is not bytes
        or type(expected) is not bytes
        or expected != _payload_fingerprint_v2(payload_bytes)
    ):
        raise LocalDatasetV2Error("training envelope issuance fingerprint does not verify")
    return value


def sealed_envelope_bytes_v2(value: object) -> bytes:
    """Return the exact canonical bytes this issued envelope was sealed from.

    Deriving an envelope from a raw record is by far the most expensive step of
    sealing -- measured at 34 records/s against 186/s for re-issuing one from
    these bytes.  A caller that must traverse the corpus twice (the sharded
    snapshot builder assigns splits globally before it can emit any example) can
    keep these bytes after the first traversal and re-issue from them, instead of
    paying the derivation again.

    The bytes carry no capability by themselves: :func:`reissue_sealed_envelope_v2`
    revalidates them in full before handing back an envelope.
    """
    return bytes(require_training_example_envelope_v2(value)._payload_bytes)


def reissue_sealed_envelope_v2(payload_bytes: bytes) -> TrainingExampleEnvelopeV2:
    """Issue an envelope from bytes a previous issuance produced.

    Runs the same validation as the dataset path -- canonical form, payload
    schema, and byte-for-byte canonical round trip -- so spooled bytes that were
    truncated or edited are refused rather than trusted.
    """
    if type(payload_bytes) is not bytes:
        raise LocalDatasetV2Error("sealed envelope bytes must be exact bytes")
    return _issue_envelope_bytes_v2(payload_bytes)


def _iter_snapshot_records(payload: bytes):
    handle = io.BytesIO(payload)
    while True:
        raw_line = handle.readline(MAX_LOCAL_RECORD_BYTES_V2 + 2)
        if not raw_line:
            break
        if not raw_line.endswith(b"\n") or raw_line == b"\n":
            raise LocalDatasetV2Error("dataset JSONL contains an invalid blank or unterminated line")
        if len(raw_line) > MAX_LOCAL_RECORD_BYTES_V2 + 1:
            raise LocalDatasetV2Error("dataset JSONL record exceeds the bounded record limit")
        yield parse_canonical_json_bytes_v2(raw_line[:-1])


def _read_spooled_envelope_line_v2(spool: Any) -> bytes | None:
    raw_line = spool.readline(MAX_LOCAL_RECORD_BYTES_V2 + 2)
    if not raw_line:
        return None
    if (
        not raw_line.endswith(b"\n") or raw_line == b"\n"
        or len(raw_line) > MAX_LOCAL_RECORD_BYTES_V2 + 1
    ):
        raise LocalDatasetV2Error("sealed envelope spool contains an invalid bounded line")
    return raw_line[:-1]


def iter_training_example_envelopes_v2(
    path: str | Path, *, manifest: dict[str, object], vocabulary: CardVocabularyV1,
    trusted_permissions: Mapping[str, TrustedPermissionV1], qualification_time_utc: str,
):
    """Yield only eligible teacher envelopes from one exact, bounded dataset snapshot."""
    manifest_data = _sealed_manifest(manifest)
    now = _utc(qualification_time_utc, field="qualification_time_utc")
    assert now is not None
    permissions = _verify_manifest_trust(manifest_data, trusted_permissions)
    try:
        snapshot = read_exact_regular_file(
            path, max_bytes=MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2,
        )
    except ExactFileSnapshotError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise exc.__cause__
        raise LocalDatasetV2Error("dataset snapshot could not be established") from exc

    expected_sources = {
        (item["kind"], item["artifact_sha256"])
        for item in manifest_data["source_artifacts"]
    }
    expected_permissions = {
        item["permission_manifest_id"] for item in manifest_data["permission_references"]
    }
    references = {
        item["permission_manifest_id"]: item for item in manifest_data["permission_references"]
    }
    descriptor, spool_path_text = tempfile.mkstemp(
        prefix="specialist-envelope-spool-", suffix=".jsonl",
    )
    spool_path = Path(spool_path_text)
    spool: Any | None = None
    try:
        os.unlink(spool_path)
        spool = os.fdopen(descriptor, "w+b", closefd=True)
        descriptor = -1
        with spool:
            seen_hashes: list[str] = []
            seen_sources: set[tuple[str, str]] = set()
            seen_permissions: set[str] = set()
            spool_bytes = 0
            eligible_count = 0
            for record in _iter_snapshot_records(snapshot.payload):
                model_input, labels = validate_local_record_v2(record, vocabulary=vocabulary)
                payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
                source = _validate_source(payload["source"])
                seen_hashes.append(payload["content_hash"])
                seen_sources.add((source["kind"], source["artifact_sha256"]))
                permission_id = source["permission_manifest_id"]
                if permission_id is not None:
                    seen_permissions.add(permission_id)
                if not _qualified_for_training(
                    source, permissions=permissions, qualification_time_utc=now,
                ):
                    continue
                if labels["teacher"]["status"] != "available":
                    continue
                loss_rows = semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary)
                if not loss_rows:
                    continue
                if type(permission_id) is not str or permission_id not in permissions:
                    raise LocalDatasetV2Error("qualified record has no verified permission")
                permission = permissions[permission_id]
                reference = references.get(permission_id)
                if reference is None:
                    raise LocalDatasetV2Error("qualified record permission is absent from the manifest")
                teacher = labels["teacher"]
                target = teacher["value_target"]
                encoded = _seal_envelope_payload_v2({
                    "model_input": model_input, "loss_rows": loss_rows,
                    "value_target": None if target is None else float(target),
                    "example_quality_weight": float(teacher["quality_weight"]),
                    "record_id": payload["record_id"], "episode_id_hash": payload["episode_id_hash"],
                    "near_duplicate_id": payload["near_duplicate_id"],
                    "record_content_hash": payload["content_hash"],
                    "source_kind": source["kind"], "source_artifact_sha256": source["artifact_sha256"],
                    "permission_manifest_id": permission_id,
                    "permission_content_hash": permission["content_hash"],
                    "permission_trusted_bytes_sha256": reference["trusted_bytes_sha256"],
                    "manifest_id": manifest_data["manifest_id"],
                    "manifest_content_hash": manifest_data["content_hash"],
                    "dataset_snapshot_sha256": snapshot.sha256,
                })
                eligible_count += 1
                if eligible_count > manifest_data["record_count"]:
                    raise LocalDatasetV2Error("eligible envelope count exceeds the manifest record count")
                spool_bytes += len(encoded) + 1
                if spool_bytes > MAX_TRAINING_ENVELOPE_SPOOL_BYTES_V2:
                    raise LocalDatasetV2Error("sealed envelope spool exceeds its bounded byte limit")
                spool.write(encoded)
                spool.write(b"\n")
            if (
                len(seen_hashes) != manifest_data["record_count"]
                or sorted(seen_hashes) != manifest_data["record_content_hashes"]
            ):
                raise LocalDatasetV2Error("dataset snapshot does not match manifest record hashes")
            if seen_sources != expected_sources or seen_permissions != expected_permissions:
                raise LocalDatasetV2Error("dataset snapshot source/permission references do not match manifest")
            spool.flush()
            os.fsync(spool.fileno())
            spool.seek(0)
            yielded = 0
            while True:
                encoded = _read_spooled_envelope_line_v2(spool)
                if encoded is None:
                    break
                yielded += 1
                if yielded > eligible_count:
                    raise LocalDatasetV2Error("sealed envelope spool count exceeds validation")
                yield _issue_envelope_bytes_v2(encoded)
            if yielded != eligible_count:
                raise LocalDatasetV2Error("sealed envelope spool count differs from validation")
    finally:
        if spool is not None:
            if not spool.closed:
                spool.close()
        elif descriptor >= 0:
            os.close(descriptor)
        if spool_path.exists():
            spool_path.unlink()


__all__ = [
    "MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2", "MAX_TRAINING_ENVELOPE_SPOOL_BYTES_V2",
    "TrainingExampleEnvelopeV2", "iter_training_example_envelopes_v2",
    "reject_forbidden_private_fields_v2", "reissue_sealed_envelope_v2",
    "require_training_example_envelope_v2", "sealed_envelope_bytes_v2",
    "semantic_action_from_training_payload_v2", "specialist_model_input_from_training_payload_v2",
]
