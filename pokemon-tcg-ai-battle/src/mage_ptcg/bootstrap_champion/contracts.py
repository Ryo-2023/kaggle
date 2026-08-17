"""Immutable, content-addressed contracts for pre-training selection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.continuous_league.contracts import (
    atomic_write_json,
    content_id,
    file_sha256,
    load_json,
    require_sha256,
)
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256


class BootstrapContractError(ValueError):
    """Fail closed when a bootstrap artifact cannot be trusted."""


class InitializationMode(StrEnum):
    DIRECT_CHECKPOINT = "DIRECT_CHECKPOINT"
    TEACHER_DISTILLATION = "TEACHER_DISTILLATION"


class DeckCompatibility(StrEnum):
    EXACT_DECK = "EXACT_DECK"
    ARBITRARY_LEGAL_DECK = "ARBITRARY_LEGAL_DECK"


def _sha(value: str, field: str) -> str:
    try:
        return require_sha256(str(value), field)
    except ValueError as exc:
        raise BootstrapContractError(str(exc)) from exc


def _nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BootstrapContractError(f"{field} must be non-empty")
    return value


@dataclass(frozen=True, slots=True)
class DeckAsset:
    deck_id: str
    deck_hash: str
    snapshot_path: str
    source_id: str
    source_hash: str

    def __post_init__(self) -> None:
        _nonempty(self.deck_id, "deck_id")
        _nonempty(self.snapshot_path, "snapshot_path")
        _nonempty(self.source_id, "source_id")
        _sha(self.deck_hash, "deck_hash")
        _sha(self.source_hash, "source_hash")
        path = Path(self.snapshot_path)
        if not path.is_file():
            raise BootstrapContractError(f"deck snapshot is missing: {path}")
        try:
            cards = [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except ValueError as exc:
            raise BootstrapContractError(f"deck snapshot is not an integer CSV: {path}") from exc
        if len(cards) != 60:
            raise BootstrapContractError("deck snapshot must contain exactly 60 cards")
        if canonical_deck_sha256(cards) != self.deck_hash:
            raise BootstrapContractError("deck snapshot hash differs from deck_hash")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PolicyAsset:
    policy_id: str
    policy_hash: str
    policy_kind: str
    runtime_path: str
    adapter_hash: str
    runtime_config_hash: str
    compatibility: DeckCompatibility
    exact_deck_hash: str | None
    source_id: str
    source_hash: str

    def __post_init__(self) -> None:
        for field in ("policy_id", "policy_kind", "runtime_path", "source_id"):
            _nonempty(str(getattr(self, field)), field)
        for field in ("policy_hash", "adapter_hash", "runtime_config_hash", "source_hash"):
            _sha(str(getattr(self, field)), field)
        if self.compatibility is DeckCompatibility.EXACT_DECK:
            if self.exact_deck_hash is None:
                raise BootstrapContractError("EXACT_DECK policy requires exact_deck_hash")
            _sha(self.exact_deck_hash, "exact_deck_hash")
        elif self.compatibility is DeckCompatibility.ARBITRARY_LEGAL_DECK:
            if self.exact_deck_hash is not None:
                raise BootstrapContractError(
                    "ARBITRARY_LEGAL_DECK policy must not set exact_deck_hash"
                )
        else:  # pragma: no cover - protects callers passing raw strings
            raise BootstrapContractError(f"unknown deck compatibility: {self.compatibility!r}")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["compatibility"] = self.compatibility.value
        return value


@dataclass(frozen=True, slots=True)
class JointCandidate:
    deck: DeckAsset
    policy: PolicyAsset
    simulator_contract_hash: str

    def __post_init__(self) -> None:
        _sha(self.simulator_contract_hash, "simulator_contract_hash")
        if (
            self.policy.compatibility is DeckCompatibility.EXACT_DECK
            and self.policy.exact_deck_hash != self.deck.deck_hash
        ):
            raise BootstrapContractError("EXACT_DECK policy cannot be paired with this deck")

    @property
    def candidate_id(self) -> str:
        return content_id(
            "bootstrap-joint-candidate-v1",
            {
                "deck_hash": self.deck.deck_hash,
                "policy_hash": self.policy.policy_hash,
                "adapter_hash": self.policy.adapter_hash,
                "runtime_config_hash": self.policy.runtime_config_hash,
                "simulator_contract_hash": self.simulator_contract_hash,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "deck": self.deck.to_dict(),
            "policy": self.policy.to_dict(),
            "simulator_contract_hash": self.simulator_contract_hash,
        }


def _champion_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "schema_version",
            "candidate_registry_id",
            "screen_benchmark_id",
            "validation_benchmark_id",
            "candidate",
            "candidate_id",
            "initialization_mode",
            "score_summary",
        )
    }


@dataclass(frozen=True, slots=True)
class BootstrapChampionManifest:
    candidate_registry_id: str
    screen_benchmark_id: str
    validation_benchmark_id: str
    candidate: JointCandidate
    initialization_mode: InitializationMode
    score_summary: Mapping[str, Any]
    bootstrap_champion_id: str

    @classmethod
    def build(
        cls,
        *,
        candidate_registry_id: str,
        screen_benchmark_id: str,
        validation_benchmark_id: str,
        candidate: JointCandidate,
        initialization_mode: InitializationMode,
        score_summary: Mapping[str, Any],
    ) -> "BootstrapChampionManifest":
        for field, value in (
            ("candidate_registry_id", candidate_registry_id),
            ("screen_benchmark_id", screen_benchmark_id),
            ("validation_benchmark_id", validation_benchmark_id),
        ):
            _sha(value, field)
        if int(score_summary.get("fault_count", 0)) != 0:
            raise BootstrapContractError("Bootstrap Champion cannot contain faults")
        document = {
            "schema_version": "bootstrap-champion-v1",
            "candidate_registry_id": candidate_registry_id,
            "screen_benchmark_id": screen_benchmark_id,
            "validation_benchmark_id": validation_benchmark_id,
            "candidate": candidate.to_dict(),
            "candidate_id": candidate.candidate_id,
            "initialization_mode": initialization_mode.value,
            "score_summary": dict(score_summary),
        }
        return cls(
            candidate_registry_id=candidate_registry_id,
            screen_benchmark_id=screen_benchmark_id,
            validation_benchmark_id=validation_benchmark_id,
            candidate=candidate,
            initialization_mode=initialization_mode,
            score_summary=dict(score_summary),
            bootstrap_champion_id=content_id("bootstrap-champion-v1", _champion_identity(document)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bootstrap-champion-v1",
            "bootstrap_champion_id": self.bootstrap_champion_id,
            "candidate_registry_id": self.candidate_registry_id,
            "screen_benchmark_id": self.screen_benchmark_id,
            "validation_benchmark_id": self.validation_benchmark_id,
            "candidate": self.candidate.to_dict(),
            "candidate_id": self.candidate.candidate_id,
            "initialization_mode": self.initialization_mode.value,
            "score_summary": dict(self.score_summary),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BootstrapChampionManifest":
        if payload.get("schema_version") != "bootstrap-champion-v1":
            raise BootstrapContractError("unsupported Bootstrap Champion schema")
        candidate_payload = payload.get("candidate")
        if not isinstance(candidate_payload, Mapping):
            raise BootstrapContractError("Bootstrap Champion candidate is missing")
        deck = DeckAsset(**dict(candidate_payload["deck"]))
        policy_data = dict(candidate_payload["policy"])
        policy_data["compatibility"] = DeckCompatibility(policy_data["compatibility"])
        candidate = JointCandidate(deck, PolicyAsset(**policy_data), str(candidate_payload["simulator_contract_hash"]))
        if payload.get("candidate_id") != candidate.candidate_id:
            raise BootstrapContractError("Bootstrap Champion candidate identity mismatch")
        rebuilt = cls.build(
            candidate_registry_id=str(payload["candidate_registry_id"]),
            screen_benchmark_id=str(payload["screen_benchmark_id"]),
            validation_benchmark_id=str(payload["validation_benchmark_id"]),
            candidate=candidate,
            initialization_mode=InitializationMode(str(payload["initialization_mode"])),
            score_summary=dict(payload["score_summary"]),
        )
        if payload.get("bootstrap_champion_id") != rebuilt.bootstrap_champion_id:
            raise BootstrapContractError("Bootstrap Champion identity mismatch")
        return rebuilt


@dataclass(frozen=True, slots=True)
class BootstrapCheckpointManifest:
    bootstrap_champion_id: str
    initialization_mode: InitializationMode
    model_config_hash: str
    action_schema_hash: str
    deck_hash: str
    online_weights_sha256: str
    teacher_dataset_id: str | None
    source_checkpoint_id: str | None
    bootstrap_checkpoint_id: str

    @classmethod
    def build(
        cls,
        *,
        bootstrap_champion_id: str,
        initialization_mode: InitializationMode,
        model_config_hash: str,
        action_schema_hash: str,
        deck_hash: str,
        online_weights_sha256: str,
        teacher_dataset_id: str | None = None,
        source_checkpoint_id: str | None = None,
    ) -> "BootstrapCheckpointManifest":
        for field, value in (
            ("bootstrap_champion_id", bootstrap_champion_id),
            ("model_config_hash", model_config_hash),
            ("action_schema_hash", action_schema_hash),
            ("deck_hash", deck_hash),
            ("online_weights_sha256", online_weights_sha256),
        ):
            _sha(value, field)
        if initialization_mode is InitializationMode.TEACHER_DISTILLATION:
            if not teacher_dataset_id or source_checkpoint_id:
                raise BootstrapContractError("distillation requires teacher_dataset_id only")
        elif initialization_mode is InitializationMode.DIRECT_CHECKPOINT:
            if not source_checkpoint_id or teacher_dataset_id:
                raise BootstrapContractError("direct initialization requires source_checkpoint_id only")
        identity = {
            "schema_version": "bootstrap-checkpoint-v1",
            "bootstrap_champion_id": bootstrap_champion_id,
            "initialization_mode": initialization_mode.value,
            "model_config_hash": model_config_hash,
            "action_schema_hash": action_schema_hash,
            "deck_hash": deck_hash,
            "online_weights_sha256": online_weights_sha256,
            "teacher_dataset_id": teacher_dataset_id,
            "source_checkpoint_id": source_checkpoint_id,
        }
        return cls(
            bootstrap_champion_id=bootstrap_champion_id,
            initialization_mode=initialization_mode,
            model_config_hash=model_config_hash,
            action_schema_hash=action_schema_hash,
            deck_hash=deck_hash,
            online_weights_sha256=online_weights_sha256,
            teacher_dataset_id=teacher_dataset_id,
            source_checkpoint_id=source_checkpoint_id,
            bootstrap_checkpoint_id=content_id("bootstrap-checkpoint-v1", identity),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bootstrap-checkpoint-v1",
            "bootstrap_checkpoint_id": self.bootstrap_checkpoint_id,
            "bootstrap_champion_id": self.bootstrap_champion_id,
            "initialization_mode": self.initialization_mode.value,
            "model_config_hash": self.model_config_hash,
            "action_schema_hash": self.action_schema_hash,
            "deck_hash": self.deck_hash,
            "online_weights_sha256": self.online_weights_sha256,
            "teacher_dataset_id": self.teacher_dataset_id,
            "source_checkpoint_id": self.source_checkpoint_id,
            "target_equals_online": True,
            "optimizer_state": "fresh",
            "global_step": 0,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BootstrapCheckpointManifest":
        if payload.get("schema_version") != "bootstrap-checkpoint-v1":
            raise BootstrapContractError("unsupported Bootstrap checkpoint schema")
        if payload.get("target_equals_online") is not True or payload.get("optimizer_state") != "fresh" or payload.get("global_step") != 0:
            raise BootstrapContractError("Bootstrap checkpoint state contract mismatch")
        rebuilt = cls.build(
            bootstrap_champion_id=str(payload["bootstrap_champion_id"]),
            initialization_mode=InitializationMode(str(payload["initialization_mode"])),
            model_config_hash=str(payload["model_config_hash"]),
            action_schema_hash=str(payload["action_schema_hash"]),
            deck_hash=str(payload["deck_hash"]),
            online_weights_sha256=str(payload["online_weights_sha256"]),
            teacher_dataset_id=payload.get("teacher_dataset_id"),
            source_checkpoint_id=payload.get("source_checkpoint_id"),
        )
        if payload.get("bootstrap_checkpoint_id") != rebuilt.bootstrap_checkpoint_id:
            raise BootstrapContractError("Bootstrap checkpoint identity mismatch")
        return rebuilt


def write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    """Write once; allow idempotent reruns but never replace content."""

    path = Path(path)
    expected = dict(payload)
    if path.exists():
        try:
            existing = load_json(path)
        except ValueError as exc:
            raise BootstrapContractError(str(exc)) from exc
        if existing != expected:
            raise BootstrapContractError(f"manifest path already has different content: {path}")
        return
    atomic_write_json(path, expected)


def weights_sha256(path: Path) -> str:
    if not Path(path).is_file():
        raise BootstrapContractError(f"weights are missing: {path}")
    return file_sha256(Path(path))
