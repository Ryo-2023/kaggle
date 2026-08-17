"""Tests verifying portability (no absolute paths) and absence of credentials."""

from __future__ import annotations
import re
from pathlib import Path

def test_source_code_portability():
    # Scan source directory for absolute path patterns
    src_dir = Path(__file__).parent.parent.parent / "src" / "mage_ptcg" / "offline_training_v1_support"
    assert src_dir.exists()

    abs_path_pattern = re.compile(r'(/home/|/mnt/|/Users/|C:\\\\)')

    # White-listed files where redaction patterns are allowed
    allowed_redactions = {
        "reproducibility.py",
        "contracts.py",
        "config_lint.py",
        "incident.py"
    }

    for path in src_dir.glob("**/*.py"):
        content = path.read_text(encoding="utf-8")
        for line_num, line in enumerate(content.splitlines(), 1):
            if abs_path_pattern.search(line):
                # Check if it is a redaction template inside allowed files
                is_allowed = False
                if path.name in allowed_redactions:
                    # Allow validation pattern listings
                    if "for p in" in line or "for path_pat in" in line or "any(" in line:
                        is_allowed = True

                assert is_allowed, f"Hardcoded absolute path pattern detected in {path.name}:{line_num} -> {line.strip()}"

def test_no_hardcoded_secrets():
    src_dir = Path(__file__).parent.parent.parent / "src" / "mage_ptcg" / "offline_training_v1_support"

    secret_key_pattern = re.compile(r'(api_key|token|oauth|password|secret|bearer)\s*=\s*["\'][a-zA-Z0-9_-]{16,}["\']', re.IGNORECASE)

    for path in src_dir.glob("**/*.py"):
        content = path.read_text(encoding="utf-8")
        for line_num, line in enumerate(content.splitlines(), 1):
            match = secret_key_pattern.search(line)
            assert not match, f"Possible hardcoded credentials detected in {path.name}:{line_num} -> {line.strip()}"
