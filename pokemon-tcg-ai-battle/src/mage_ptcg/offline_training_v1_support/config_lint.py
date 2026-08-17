"""Configuration validator and linter module.

Validates parameter bounds, illegal paths, missing keys, split ratios,
and warns on unsafe defaults or absolute path leakage.
"""

from __future__ import annotations

import math
from typing import Any

class ConfigLinter:
    """Validates configuration dictionaries for offline training workflows."""

    def lint(self, config: dict[str, Any]) -> dict[str, Any]:
        """Perform static checks on configuration and return issues with suggestions."""
        errors = []
        warnings = []
        suggestions = []

        # 1. Unknown & Required Keys Checks
        supported_keys = {
            "schema_version", "seed", "worker_count", "split_ratios",
            "confidence_level", "preset", "environment", "output_dir",
            "max_repetitions", "is_validation_split", "is_test_split"
        }
        required_keys = {"schema_version", "seed"}

        for k in required_keys:
            if k not in config:
                errors.append(f"Missing required key: {k}")
                suggestions.append(f"Add required key '{k}' to configuration")

        for k in config:
            if k not in supported_keys:
                warnings.append(f"Unknown key detected: {k}")
                suggestions.append(f"Remove unknown key '{k}' to clean config")

        # 2. Type and Value Validity
        # Seed
        seed = config.get("seed")
        if seed is not None:
            if not isinstance(seed, int) or seed < 0:
                errors.append(f"Invalid seed: {seed}. Must be a non-negative integer.")
                suggestions.append("Set 'seed' to a positive integer (e.g. 42)")

        # Worker count
        workers = config.get("worker_count")
        if workers is not None:
            if not isinstance(workers, int) or workers <= 0:
                errors.append(f"Invalid worker_count: {workers}. Must be a positive integer.")
            elif workers > 32:
                # Hard max constraint warning
                warnings.append(f"High worker_count: {workers}. Exceeds hard recommended maximum of 32.")
                suggestions.append("Lower 'worker_count' to 32 or fewer to avoid context-switching overhead")

        # Confidence level
        conf = config.get("confidence_level")
        if conf is not None:
            if not isinstance(conf, (int, float)) or not math.isfinite(conf) or conf <= 0.0 or conf >= 1.0:
                errors.append(f"Invalid confidence_level: {conf}. Must be float in range (0.0, 1.0).")
                suggestions.append("Set 'confidence_level' to a value like 0.95 or 0.99")

        # Split ratios
        splits = config.get("split_ratios")
        if splits is not None:
            if not isinstance(splits, dict):
                errors.append("split_ratios must be a dictionary (e.g. {'train': 0.8, 'val': 0.2})")
            else:
                total = sum(v for v in splits.values() if isinstance(v, (int, float)))
                if not math.isclose(total, 1.0, rel_tol=1e-6):
                    errors.append(f"Invalid split ratios sum: {total}. Sum must equal exactly 1.0.")
                    suggestions.append("Adjust split ratios so that train + val + test equal exactly 1.0")

        # Preset enum
        preset = config.get("preset")
        if preset is not None:
            valid_presets = {"debug", "development", "production"}
            if preset not in valid_presets:
                errors.append(f"Invalid preset: {preset}. Supported: {sorted(list(valid_presets))}")
            elif preset == "production":
                # Warn if production preset accidentally selected in local or test settings
                if config.get("environment") == "local":
                    warnings.append("Production preset selected while environment is set to 'local'.")
                    suggestions.append("Verify if production preset was intended for local run")

        # Path Safety Checks (absolute paths local path leakage)
        out_dir = config.get("output_dir")
        if out_dir is not None:
            if isinstance(out_dir, str):
                if any(out_dir.startswith(p) for p in ("/home/", "/mnt/", "/Users/", "C:\\")):
                    warnings.append(f"Absolute local path detected in output_dir: {out_dir}")
                    suggestions.append("Use relative paths for portability across developer workspaces")
                if ".." in out_dir:
                    warnings.append(f"Directory traversal detected in output_dir path: {out_dir}")

        # Incompatible option check
        if config.get("is_validation_split") and config.get("is_test_split"):
            errors.append("Incompatible options: is_validation_split and is_test_split cannot both be True")
            suggestions.append("Set only one split type flag to True")

        # Overall Status
        if errors:
            status = "INVALID"
        elif warnings:
            status = "VALID_WITH_WARNINGS"
        else:
            status = "VALID"

        return {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
        }
