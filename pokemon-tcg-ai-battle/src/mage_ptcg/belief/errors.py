"""Validation errors for exact belief core values."""

from __future__ import annotations

from typing import TypeAlias


PathComponent: TypeAlias = str | int
ErrorPath: TypeAlias = tuple[PathComponent, ...]


def _format_path(path: ErrorPath) -> str:
    rendered = "$"
    for component in path:
        if type(component) is int:
            rendered += f"[{component}]"
        else:
            rendered += f".{component}"
    return rendered


class BeliefValidationError(ValueError):
    """A stable, machine-readable validation failure."""

    def __init__(self, code: str, path: ErrorPath, message: str) -> None:
        if type(code) is not str or not code:
            raise TypeError("code must be a non-empty str")
        if type(path) is not tuple or any(
            type(component) not in (str, int) for component in path
        ):
            raise TypeError("path must be tuple[str | int, ...]")
        if type(message) is not str or not message:
            raise TypeError("message must be a non-empty str")

        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {_format_path(path)}: {message}")


__all__ = ["BeliefValidationError", "ErrorPath", "PathComponent"]
