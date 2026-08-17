"""Shared O6 opponent-platform error type.

Split out of ``core.py`` so that lower-level, single-responsibility modules
(``runtime_closure``, ``trajectory``) can raise/catch the same typed error
without importing the higher-level ``core`` module and creating a circular
import.
"""
from __future__ import annotations


class OpponentError(RuntimeError):
    """Typed user-facing opponent platform error."""
