"""Dependency-free shared contract types."""

from typing import NewType


CardId = NewType("CardId", int)


__all__ = ["CardId"]
