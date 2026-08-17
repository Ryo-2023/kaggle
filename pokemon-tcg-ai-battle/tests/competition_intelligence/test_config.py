"""Config tests: defaults, unknown-key rejection, dangerous-setting rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence.config import (
    CONFIG_SCHEMA_VERSION,
    CompetitionIntelligenceConfig,
    ConfigError,
    load_config,
)


class TestDefaults:
    def test_default_construction_is_safe(self) -> None:
        config = CompetitionIntelligenceConfig()
        assert config.automation.auto_promote is False
        assert config.automation.auto_submit is False
        assert config.external.public_other_training_enabled is False

    def test_content_hash_is_deterministic(self) -> None:
        a = CompetitionIntelligenceConfig()
        b = CompetitionIntelligenceConfig()
        assert a.content_hash() == b.content_hash()


class TestLoadConfig:
    def test_load_from_mapping_with_wrapper_key(self) -> None:
        config = load_config({"competition_intelligence": {"schema_version": CONFIG_SCHEMA_VERSION, "run_root": "runs/x"}})
        assert config.run_root == "runs/x"

    def test_load_from_bare_mapping(self) -> None:
        config = load_config({"schema_version": CONFIG_SCHEMA_VERSION, "run_root": "runs/y"})
        assert config.run_root == "runs/y"

    def test_load_from_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"schema_version": CONFIG_SCHEMA_VERSION, "run_root": "runs/z"}), encoding="utf-8")
        config = load_config(path)
        assert config.run_root == "runs/z"

    def test_rejects_unknown_top_level_key(self) -> None:
        with pytest.raises(ConfigError):
            load_config({"schema_version": CONFIG_SCHEMA_VERSION, "not_a_real_key": True})

    def test_rejects_unknown_key_in_section(self) -> None:
        with pytest.raises(ConfigError):
            load_config({"schema_version": CONFIG_SCHEMA_VERSION, "automation": {"auto_promote": False, "bogus": 1}})

    def test_nested_config_sections_apply(self) -> None:
        config = load_config({
            "schema_version": CONFIG_SCHEMA_VERSION,
            "analytics": {"temporal_decay": 0.9, "minimum_cluster_support": 10},
        })
        assert config.analytics.temporal_decay == 0.9
        assert config.analytics.minimum_cluster_support == 10


class TestDangerousSettingsRejected:
    def test_auto_promote_true_is_rejected(self) -> None:
        with pytest.raises(ConfigError):
            load_config({"schema_version": CONFIG_SCHEMA_VERSION, "automation": {"auto_promote": True}})

    def test_auto_submit_true_is_rejected(self) -> None:
        with pytest.raises(ConfigError):
            load_config({"schema_version": CONFIG_SCHEMA_VERSION, "automation": {"auto_submit": True}})

    def test_public_other_training_enabled_true_is_rejected(self) -> None:
        with pytest.raises(ConfigError):
            load_config({"schema_version": CONFIG_SCHEMA_VERSION, "external": {"public_other_training_enabled": True}})

    def test_analytics_temporal_decay_out_of_range_rejected(self) -> None:
        with pytest.raises(ConfigError):
            load_config({"schema_version": CONFIG_SCHEMA_VERSION, "analytics": {"temporal_decay": 1.5}})

    def test_normalization_unknown_fields_must_be_known_enum(self) -> None:
        with pytest.raises(ConfigError):
            load_config({"schema_version": CONFIG_SCHEMA_VERSION, "normalization": {"unknown_fields": "ignore_silently"}})
