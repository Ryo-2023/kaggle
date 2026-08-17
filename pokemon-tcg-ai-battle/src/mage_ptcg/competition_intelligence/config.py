"""Configuration schema for the Competition Intelligence sidecar.

Mirrors ``mage_ptcg.offline_training.config``'s conventions: a frozen
dataclass tree, ``load_config()`` accepting either a JSON path or an
in-memory mapping, and fail-closed rejection of unknown keys.
``CompetitionIntelligenceConfig.__post_init__`` additionally rejects the
specific dangerous settings the design marks as forbidden in v1
(auto-promotion, auto-submission, training on default-deny PUBLIC_OTHER
sources) — a config file cannot silently re-enable them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical import digest

CONFIG_SCHEMA_VERSION = "competition-intelligence-config-v1"


class ConfigError(ValueError):
    """Raised on an unknown key, wrong type, or a v1-forbidden dangerous setting."""


def _reject_unknown_keys(data: Mapping[str, Any], allowed: set[str], *, section: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"unknown key(s) in {section}: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class PermissionsConfig:
    default_allowed_uses: tuple[str, ...] = ("ARCHIVE",)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PermissionsConfig":
        _reject_unknown_keys(data, {"default_allowed_uses"}, section="permissions")
        raw = data.get("default_allowed_uses", ["ARCHIVE"])
        if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
            raise ConfigError("permissions.default_allowed_uses must be a list of strings")
        return cls(default_allowed_uses=tuple(raw))


@dataclass(frozen=True, slots=True)
class ArchiveConfig:
    content_addressed: bool = True
    verify_hash_on_read: bool = True
    atomic_write: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ArchiveConfig":
        allowed = {"content_addressed", "verify_hash_on_read", "atomic_write"}
        _reject_unknown_keys(data, allowed, section="archive")
        return cls(**{key: bool(value) for key, value in data.items()})


@dataclass(frozen=True, slots=True)
class NormalizationConfig:
    unknown_fields: str = "preserve"
    strict_required_fields: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "NormalizationConfig":
        allowed = {"unknown_fields", "strict_required_fields"}
        _reject_unknown_keys(data, allowed, section="normalization")
        unknown_fields = data.get("unknown_fields", "preserve")
        if unknown_fields not in ("preserve", "reject"):
            raise ConfigError("normalization.unknown_fields must be 'preserve' or 'reject'")
        strict = bool(data.get("strict_required_fields", True))
        return cls(unknown_fields=unknown_fields, strict_required_fields=strict)


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    temporal_decay: float = 0.95
    duplicate_discount: bool = True
    minimum_cluster_support: int = 5
    failure_hypothesis_threshold: float = 0.5

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AnalyticsConfig":
        allowed = {"temporal_decay", "duplicate_discount", "minimum_cluster_support", "failure_hypothesis_threshold"}
        _reject_unknown_keys(data, allowed, section="analytics")
        decay = float(data.get("temporal_decay", 0.95))
        if not (0.0 < decay <= 1.0):
            raise ConfigError("analytics.temporal_decay must be in (0, 1]")
        support = int(data.get("minimum_cluster_support", 5))
        if support < 1:
            raise ConfigError("analytics.minimum_cluster_support must be >= 1")
        threshold = float(data.get("failure_hypothesis_threshold", 0.5))
        if not (0.0 <= threshold <= 1.0):
            raise ConfigError("analytics.failure_hypothesis_threshold must be in [0, 1]")
        return cls(
            temporal_decay=decay,
            duplicate_discount=bool(data.get("duplicate_discount", True)),
            minimum_cluster_support=support,
            failure_hypothesis_threshold=threshold,
        )


@dataclass(frozen=True, slots=True)
class SnapshotsConfig:
    require_cutoff: bool = True
    enforce_allowed_use: bool = True
    split_by_episode: bool = True
    split_by_opponent: bool = True
    temporal_holdout: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SnapshotsConfig":
        allowed = {
            "require_cutoff", "enforce_allowed_use", "split_by_episode", "split_by_opponent", "temporal_holdout",
        }
        _reject_unknown_keys(data, allowed, section="snapshots")
        return cls(**{key: bool(value) for key, value in data.items()})


@dataclass(frozen=True, slots=True)
class ExternalConfig:
    kaggle_live_enabled: bool = False
    team_bundle_enabled: bool = True
    public_other_training_enabled: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ExternalConfig":
        allowed = {"kaggle_live_enabled", "team_bundle_enabled", "public_other_training_enabled"}
        _reject_unknown_keys(data, allowed, section="external")
        return cls(**{key: bool(value) for key, value in data.items()})


@dataclass(frozen=True, slots=True)
class AutomationConfig:
    auto_promote: bool = False
    auto_submit: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AutomationConfig":
        allowed = {"auto_promote", "auto_submit"}
        _reject_unknown_keys(data, allowed, section="automation")
        return cls(**{key: bool(value) for key, value in data.items()})


_TOP_LEVEL_KEYS = {
    "schema_version", "run_root", "deterministic", "permissions", "archive",
    "normalization", "analytics", "snapshots", "external", "automation",
}


@dataclass(frozen=True, slots=True)
class CompetitionIntelligenceConfig:
    schema_version: str = CONFIG_SCHEMA_VERSION
    run_root: str = "runs/competition-intelligence"
    deterministic: bool = True
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    snapshots: SnapshotsConfig = field(default_factory=SnapshotsConfig)
    external: ExternalConfig = field(default_factory=ExternalConfig)
    automation: AutomationConfig = field(default_factory=AutomationConfig)

    def __post_init__(self) -> None:
        if self.schema_version != CONFIG_SCHEMA_VERSION:
            raise ConfigError(f"unsupported config schema_version {self.schema_version!r}")
        if not self.run_root:
            raise ConfigError("run_root must be a non-empty path")
        if self.automation.auto_promote:
            raise ConfigError(
                "automation.auto_promote=true is forbidden in v1: Champion/default promotion must stay a human decision"
            )
        if self.automation.auto_submit:
            raise ConfigError("automation.auto_submit=true is forbidden in v1: Kaggle submission must stay a human decision")
        if self.external.public_other_training_enabled:
            raise ConfigError(
                "external.public_other_training_enabled=true is forbidden in v1: "
                "PUBLIC_OTHER sources default-deny TRAINING regardless of this flag"
            )

    def content_hash(self) -> str:
        return digest(asdict(self), domain="config")


_SECTION_BUILDERS = {
    "permissions": PermissionsConfig.from_mapping,
    "archive": ArchiveConfig.from_mapping,
    "normalization": NormalizationConfig.from_mapping,
    "analytics": AnalyticsConfig.from_mapping,
    "snapshots": SnapshotsConfig.from_mapping,
    "external": ExternalConfig.from_mapping,
    "automation": AutomationConfig.from_mapping,
}


def load_config(source: str | Path | Mapping[str, Any]) -> CompetitionIntelligenceConfig:
    """Load a ``CompetitionIntelligenceConfig`` from a JSON path or a mapping.

    Accepts either the bare config body, or a mapping with a top-level
    ``competition_intelligence`` key wrapping it (matching the nested YAML
    shape shown in the design doc); unknown keys at any level are rejected.
    """
    if isinstance(source, (str, Path)):
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        raw = source
    if not isinstance(raw, Mapping):
        raise ConfigError("config source must be a JSON object or a mapping")
    body = raw["competition_intelligence"] if "competition_intelligence" in raw and len(raw) == 1 else raw
    if not isinstance(body, Mapping):
        raise ConfigError("competition_intelligence config body must be a mapping")
    _reject_unknown_keys(body, _TOP_LEVEL_KEYS, section="competition_intelligence")
    sections = {
        name: builder(body.get(name, {})) for name, builder in _SECTION_BUILDERS.items()
    }
    return CompetitionIntelligenceConfig(
        schema_version=body.get("schema_version", CONFIG_SCHEMA_VERSION),
        run_root=body.get("run_root", "runs/competition-intelligence"),
        deterministic=bool(body.get("deterministic", True)),
        **sections,
    )


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "AnalyticsConfig",
    "ArchiveConfig",
    "AutomationConfig",
    "CompetitionIntelligenceConfig",
    "ConfigError",
    "ExternalConfig",
    "NormalizationConfig",
    "PermissionsConfig",
    "SnapshotsConfig",
    "load_config",
]
