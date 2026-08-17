"""Tests for common error taxonomy."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.errors import (
    SupportError,
    ValidationError,
    PrivacyError,
    LockError,
    ChecksumError
)

def test_support_error_metadata():
    err = SupportError("TEST_CODE", "Public info message", {"secret_key": "private_val"})
    assert err.code == "TEST_CODE"
    assert err.public_message == "Public info message"
    assert err.private_context == {"secret_key": "private_val"}
    assert err.severity == "ERROR"
    assert err.retryable is False

def test_bounded_message():
    long_msg = "A" * 2000
    err = ValidationError(long_msg)
    assert len(err.public_message) == 1024
    assert err.code == "VAL_ERR"

def test_privacy_error_no_leak():
    err = PrivacyError("Access denied", {"user_id": 1234, "path": "/root/secret"})
    # Public message must not leak context
    assert "user_id" not in err.public_message
    assert "secret" not in err.public_message
    assert err.private_context["user_id"] == 1234

def test_retryable_flag():
    err = LockError("Could not acquire lock")
    assert err.retryable is True
    assert err.severity == "ERROR"

def test_checksum_error():
    err = ChecksumError("Hash mismatch", {"expected": "abc", "actual": "def"})
    assert err.code == "CHECKSUM_ERR"
    assert err.private_context["expected"] == "abc"
