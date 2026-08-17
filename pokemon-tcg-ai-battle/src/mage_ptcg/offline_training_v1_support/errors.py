"""Common error taxonomy for offline training support platform.

Provides secure exception classes with separated safe messages and private contexts.
"""

from __future__ import annotations
from typing import Any, Optional

class SupportError(Exception):
    """Base exception class for all support platform errors."""

    def __init__(
        self,
        code: str,
        message: str,
        private_context: Optional[dict[str, Any]] = None,
        severity: str = "ERROR",
        retryable: bool = False,
    ) -> None:
        # Bounded message to prevent memory/output exhaustion
        bounded_message = str(message)[:1024]
        super().__init__(bounded_message)

        self.code = code
        self.public_message = bounded_message
        # Do not leak raw inputs or private variables in public_message!
        self.private_context = private_context or {}
        self.severity = severity
        self.retryable = retryable

class ValidationError(SupportError):
    """Raised when validation fails."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("VAL_ERR", message, private_context, severity="WARNING", retryable=False)

class SchemaError(SupportError):
    """Raised when schema validation fails."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("SCHEMA_ERR", message, private_context, severity="ERROR", retryable=False)

class PrivacyError(SupportError):
    """Raised when privacy violations are detected."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("PRIVACY_ERR", message, private_context, severity="CRITICAL", retryable=False)

class IntegrityError(SupportError):
    """Raised when data integrity issues are detected."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("INTEGRITY_ERR", message, private_context, severity="ERROR", retryable=False)

class ChecksumError(IntegrityError):
    """Raised when checksum mismatch occurs."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, private_context)
        self.code = "CHECKSUM_ERR"

class ConflictError(SupportError):
    """Raised when resource conflict happens."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("CONFLICT_ERR", message, private_context, severity="WARNING", retryable=True)

class LockError(SupportError):
    """Raised when locking failures happen."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("LOCK_ERR", message, private_context, severity="ERROR", retryable=True)

class ConcurrencyError(SupportError):
    """Raised when concurrency collisions occur."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("CONCURRENCY_ERR", message, private_context, severity="ERROR", retryable=True)

class CorruptionError(SupportError):
    """Raised when registries or caches are corrupted."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("CORRUPTION_ERR", message, private_context, severity="CRITICAL", retryable=False)

class CompatibilityError(SupportError):
    """Raised when components are incompatible."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("COMPATIBILITY_ERR", message, private_context, severity="ERROR", retryable=False)

class UnsupportedVersionError(CompatibilityError):
    """Raised when version is not supported."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, private_context)
        self.code = "UNSUPPORTED_VERSION_ERR"

class ResourceLimitError(SupportError):
    """Raised when resource budget is exceeded."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("RESOURCE_LIMIT_ERR", message, private_context, severity="ERROR", retryable=False)

class InsufficientEvidenceError(SupportError):
    """Raised when insufficient evidence is provided for promotion."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("INSUFFICIENT_EVIDENCE_ERR", message, private_context, severity="WARNING", retryable=False)

class UnsafeOperationError(SupportError):
    """Raised when unsafe execution is blocked."""
    def __init__(self, message: str, private_context: Optional[dict[str, Any]] = None) -> None:
        super().__init__("UNSAFE_OPERATION_ERR", message, private_context, severity="CRITICAL", retryable=False)
