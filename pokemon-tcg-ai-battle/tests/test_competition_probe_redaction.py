"""Redaction is derived, deterministic, and leaves no detected secret behind."""

from __future__ import annotations

from mage_ptcg.competition.redaction import redact_value, secret_scan


def test_redaction_removes_sensitive_values_and_home_paths() -> None:
    original = {
        "Authorization": "Bearer a-long-sensitive-token",
        "cookie": "session=private-value",
        "message": "contact user@example.com at /home/alice/.kaggle/kaggle.json https://x/?signature=abc",
    }
    redacted = redact_value(original)
    assert original["Authorization"] == "Bearer a-long-sensitive-token"
    assert redacted["Authorization"] == "<REDACTED>"
    assert redacted["cookie"] == "<REDACTED>"
    assert "user@example.com" not in redacted["message"]
    assert "/home/alice" not in redacted["message"]
    assert "signature=abc" not in redacted["message"]
    assert secret_scan(redacted) == []


def test_secret_scan_detects_raw_credential_and_signed_url_without_echoing_value() -> None:
    findings = secret_scan({"token": "private-value", "url": "https://x/?X-Amz-Signature=abc"})
    assert findings
    assert all("private-value" not in item and "abc" not in item for item in findings)
