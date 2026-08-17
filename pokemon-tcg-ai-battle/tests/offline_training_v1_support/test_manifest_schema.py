"""Tests for validation of the integration manifest schema and determinism."""

from __future__ import annotations
import json
import importlib
import pkgutil
from pathlib import Path
import pytest
import mage_ptcg.offline_training_v1_support as pkg

MANIFEST_PATH = Path(__file__).parent.parent.parent / "configs" / "offline_training_v1" / "gemini_support_integration_manifest.json"

def test_manifest_file_exists_and_valid():
    assert MANIFEST_PATH.exists()
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["schema_version"] == "gemini-support-integration-manifest-v1"
    assert "features" in data
    assert isinstance(data["features"], list)

def test_manifest_schema_and_determinism():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data["features"]
    names = [f["name"] for f in features]

    # Check alphabetical ordering determinism
    assert names == sorted(names), "Features list in manifest must be sorted by name alphabetically."

    required_keys = {
        "name", "classification", "source_modules", "tests",
        "public_entrypoints", "dependencies", "writes_to_disk",
        "privacy_risk", "overlap_with_core", "recommended_target_namespace",
        "integration_order", "acceptance_tests", "reason"
    }

    for item in features:
        for key in required_keys:
            assert key in item, f"Missing key '{key}' in feature '{item.get('name')}'"

        assert item["classification"] in ("P0", "P1", "HOLD")
        assert isinstance(item["source_modules"], list)
        assert isinstance(item["tests"], list)
        assert isinstance(item["public_entrypoints"], list)
        assert isinstance(item["dependencies"], list)
        assert isinstance(item["writes_to_disk"], bool)
        assert item["privacy_risk"] in ("low", "medium", "high")
        assert item["overlap_with_core"] in ("low", "medium", "high")
        assert isinstance(item["recommended_target_namespace"], str)
        assert isinstance(item["integration_order"], int)
        assert isinstance(item["acceptance_tests"], list)
        assert isinstance(item["reason"], str)

def test_import_side_effects_and_optional_dependencies():
    # Verify importing support package doesn't create files unexpectedly
    workspace_root = Path(__file__).parent.parent.parent
    before_files = set(workspace_root.glob("**/*"))

    for mod_info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        try:
            importlib.import_module(mod_info.name)
        except ImportError as e:
            pytest.fail(f"Failed to import support module: {mod_info.name} due to {e}")

    after_files = set(workspace_root.glob("**/*"))
    diff = after_files - before_files

    # Ignore cache files and virtualenv modifications
    real_diff = [
        p for p in diff
        if "__pycache__" not in p.parts
        and p.suffix != ".pyc"
        and ".venv" not in p.parts
    ]
    assert len(real_diff) == 0, f"Importing support modules created unexpected files: {real_diff}"
