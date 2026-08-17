"""Validated configuration for the Offline Training v1 unified pipeline.

The config is intentionally explicit and fail-closed.  Every field has a
documented meaning; unknown top-level sections are rejected so a typo can never
silently disable a safety-relevant phase.  The resolved config records the
exact values that a run executed with, together with a content hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONFIG_SCHEMA_VERSION = "offline-training-v1-config-v1"

_MODEL_PRESETS: dict[str, list[int]] = {
    "tiny": [64],
    "compact": [128, 64],
    "medium": [256, 128, 64],
}

_COLLECTION_SOURCES = frozenset({"fixture", "actual"})


class ConfigError(ValueError):
    """Raised when a preset or override cannot form a safe run configuration."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def config_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(value)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CollectionConfig:
    source: str = "fixture"
    games: int = 4
    base_seed: int = 1000
    max_steps: int = 10_000
    validation_percent: int = 20
    split_seed: int = 0
    fixture_decisions_per_seat: int = 3
    fixture_option_count: int = 3
    max_retries: int = 2

    def validate(self) -> None:
        if self.source not in _COLLECTION_SOURCES:
            raise ConfigError(f"collection.source must be one of {sorted(_COLLECTION_SOURCES)}")
        if type(self.games) is not int or self.games < 1:
            raise ConfigError("collection.games must be a positive integer")
        if type(self.base_seed) is not int or self.base_seed < 0:
            raise ConfigError("collection.base_seed must be a non-negative integer")
        if type(self.max_steps) is not int or self.max_steps < 1:
            raise ConfigError("collection.max_steps must be a positive integer")
        if not 1 <= self.validation_percent < 100:
            raise ConfigError("collection.validation_percent must be in [1, 99]")
        if type(self.fixture_decisions_per_seat) is not int or self.fixture_decisions_per_seat < 1:
            raise ConfigError("collection.fixture_decisions_per_seat must be a positive integer")
        if type(self.fixture_option_count) is not int or self.fixture_option_count < 2:
            raise ConfigError("collection.fixture_option_count must be >= 2")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ConfigError("collection.max_retries must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    shard_size: int = 128
    train_fraction: float = 0.80
    validation_fraction: float = 0.10
    test_fraction: float = 0.10
    split_seed: int = 12345

    def validate(self) -> None:
        if type(self.shard_size) is not int or self.shard_size < 1:
            raise ConfigError("dataset.shard_size must be a positive integer")
        fractions = (self.train_fraction, self.validation_fraction, self.test_fraction)
        if any((type(value) not in (int, float)) or value <= 0 for value in fractions):
            raise ConfigError("dataset split fractions must be positive numbers")
        total = sum(float(value) for value in fractions)
        if abs(total - 1.0) > 1e-6:
            raise ConfigError("dataset split fractions must sum to 1.0")


@dataclass(frozen=True, slots=True)
class ModelConfig:
    preset: str = "compact"
    hidden_dims: list[int] | None = None
    activation: str = "relu"

    def resolved_hidden_dims(self) -> list[int]:
        if self.hidden_dims is not None:
            return list(self.hidden_dims)
        return list(_MODEL_PRESETS[self.preset])

    def validate(self) -> None:
        if self.hidden_dims is None and self.preset not in _MODEL_PRESETS:
            raise ConfigError(f"model.preset must be one of {sorted(_MODEL_PRESETS)}")
        if self.hidden_dims is not None:
            if not self.hidden_dims or any(type(value) is not int or value < 1 for value in self.hidden_dims):
                raise ConfigError("model.hidden_dims must be positive integers")
        if self.activation != "relu":
            raise ConfigError("model.activation only supports 'relu' in v1")


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    epochs: int = 2
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    grad_clip: float = 1.0
    patience: int = 5
    seed: int = 7
    max_batch_decisions: int = 256
    normalize_features: bool = True
    device: str = "auto"

    def validate(self) -> None:
        if type(self.epochs) is not int or self.epochs < 1:
            raise ConfigError("training.epochs must be a positive integer")
        if type(self.learning_rate) not in (int, float) or self.learning_rate <= 0:
            raise ConfigError("training.learning_rate must be positive")
        if type(self.weight_decay) not in (int, float) or self.weight_decay < 0:
            raise ConfigError("training.weight_decay must be non-negative")
        if type(self.grad_clip) not in (int, float) or self.grad_clip <= 0:
            raise ConfigError("training.grad_clip must be positive")
        if type(self.patience) is not int or self.patience < 1:
            raise ConfigError("training.patience must be a positive integer")
        if type(self.max_batch_decisions) is not int or self.max_batch_decisions < 1:
            raise ConfigError("training.max_batch_decisions must be a positive integer")
        if self.device not in {"auto", "cpu", "cuda"}:
            raise ConfigError("training.device must be auto|cpu|cuda")


@dataclass(frozen=True, slots=True)
class ScreeningConfig:
    games: int = 4
    base_seed: int = 5000

    def validate(self) -> None:
        if type(self.games) is not int or self.games < 2:
            raise ConfigError("screening.games must be >= 2")
        if self.games % 2 != 0:
            raise ConfigError("screening.games must be even for seat balance")
        if type(self.base_seed) is not int or self.base_seed < 0:
            raise ConfigError("screening.base_seed must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class OfflineTrainingConfig:
    schema_version: str
    profile: str
    run_id_prefix: str
    collection: CollectionConfig
    dataset: DatasetConfig
    model: ModelConfig
    training: TrainingConfig
    screening: ScreeningConfig

    def validate(self) -> None:
        if self.schema_version != CONFIG_SCHEMA_VERSION:
            raise ConfigError("unsupported config schema version")
        if not self.profile or "/" in self.profile:
            raise ConfigError("profile must be a non-empty path-safe string")
        if not self.run_id_prefix or "/" in self.run_id_prefix:
            raise ConfigError("run_id_prefix must be a non-empty path-safe string")
        for section in (self.collection, self.dataset, self.model, self.training, self.screening):
            section.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "run_id_prefix": self.run_id_prefix,
            "collection": asdict(self.collection),
            "dataset": asdict(self.dataset),
            "model": asdict(self.model),
            "training": asdict(self.training),
            "screening": asdict(self.screening),
        }

    def hash(self) -> str:
        return config_hash(self.to_dict())


def _section(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"config section {name} must be an object")
    return dict(value)


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"unknown {where} keys: {sorted(unknown)}")


def _build_section(cls: type, raw: Mapping[str, Any], name: str):
    allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    _reject_unknown(raw, allowed, f"{name} config")
    return cls(**raw)


def load_config(source: str | Path | Mapping[str, Any]) -> OfflineTrainingConfig:
    """Load and fully validate a config from a JSON file path or mapping."""
    if isinstance(source, Mapping):
        raw: Mapping[str, Any] = source
    else:
        text = Path(source).read_text(encoding="utf-8")
        parsed = json.loads(text)
        if not isinstance(parsed, Mapping):
            raise ConfigError("config file must contain a JSON object")
        raw = parsed
    allowed_top = {"schema_version", "profile", "run_id_prefix", "collection", "dataset", "model", "training", "screening"}
    _reject_unknown(raw, allowed_top, "top-level")
    config = OfflineTrainingConfig(
        schema_version=str(raw.get("schema_version", CONFIG_SCHEMA_VERSION)),
        profile=str(raw.get("profile", "smoke")),
        run_id_prefix=str(raw.get("run_id_prefix", "offline-training-v1")),
        collection=_build_section(CollectionConfig, _section(raw, "collection"), "collection"),
        dataset=_build_section(DatasetConfig, _section(raw, "dataset"), "dataset"),
        model=_build_section(ModelConfig, _section(raw, "model"), "model"),
        training=_build_section(TrainingConfig, _section(raw, "training"), "training"),
        screening=_build_section(ScreeningConfig, _section(raw, "screening"), "screening"),
    )
    config.validate()
    return config


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "CollectionConfig",
    "ConfigError",
    "DatasetConfig",
    "ModelConfig",
    "OfflineTrainingConfig",
    "ScreeningConfig",
    "TrainingConfig",
    "config_hash",
    "load_config",
]
