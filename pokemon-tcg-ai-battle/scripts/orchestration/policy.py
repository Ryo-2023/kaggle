"""Deterministic path and command policy enforcement."""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


class PolicyViolation(RuntimeError):
    """Raised when an untrusted worker violates a fixed policy."""


def normalize_relative(path: str) -> str:
    """Return a normalized repository-relative POSIX path or raise."""

    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise PolicyViolation(f"unsafe relative path: {path}")
    normalized = candidate.as_posix()
    if normalized in {".", ".git"} or normalized.startswith(".git/"):
        raise PolicyViolation(f"reserved path: {path}")
    return normalized


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    """Return whether *path* equals, descends from, or globs a policy pattern."""

    normalized = normalize_relative(path)
    for raw_pattern in patterns:
        pattern = normalize_relative(raw_pattern)
        if normalized == pattern or normalized.startswith(pattern.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
    return False


# These paths change orchestration authority, dependency resolution, or submission
# behavior. They must always be high risk and require human integration.
CONTROL_PLANE_PATTERNS = (
    "scripts/orchestrate.py",
    "scripts/orchestration/**",
    ".orchestrator/**",
    ".github/**",
    ".gitmodules",
    ".gitattributes",
    ".gitignore",
    "**/.gitmodules",
    "**/.gitattributes",
    "**/.gitignore",
    ".gitconfig",
    "**/.gitconfig",
    "requirements*.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "package-lock.json",
    "package.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    "environment*.yml",
    "environment*.yaml",
    "conda*.yml",
    "conda*.yaml",
    "**/*authorization*",
    "**/*secret*",
    "**/*credential*",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "kaggle.json",
    "submissions/**",
    "**/*submission*",
    "**/*kaggle*",
)

_CONTROL_PLANE_SENTINELS = (
    "scripts/orchestrate.py",
    "scripts/orchestration/kernel.py",
    ".orchestrator/policies/external_model_authorization.json",
    ".github/workflows/test.yml",
    ".gitmodules",
    ".gitattributes",
    ".gitignore",
    "requirements.txt",
    "src/authorization_policy.py",
    "submissions/entry.zip",
)


def is_control_plane_path(path: str) -> bool:
    """Return whether one repository-relative path is Control Plane scope."""

    return path_matches(path, CONTROL_PLANE_PATTERNS)


def touches_control_plane(paths: Iterable[str]) -> bool:
    """Conservatively detect exact paths or allowed globs intersecting policy."""

    return any(
        is_control_plane_path(path)
        or any(path_matches(sentinel, (path,)) for sentinel in _CONTROL_PLANE_SENTINELS)
        for path in paths
    )


def validate_changed_paths(
    root: Path,
    changed_paths: Iterable[str],
    allowed_paths: Iterable[str],
    forbidden_paths: Iterable[str],
    protected_paths: Iterable[str],
) -> tuple[str, ...]:
    """Validate changed paths and reject symlink escapes from *root*."""

    root_real = root.resolve()
    validated: list[str] = []
    for raw_path in changed_paths:
        path = normalize_relative(raw_path)
        if not path_matches(path, allowed_paths):
            raise PolicyViolation(f"change outside allowed_paths: {path}")
        if path_matches(path, forbidden_paths):
            raise PolicyViolation(f"forbidden path changed: {path}")
        if path_matches(path, protected_paths):
            raise PolicyViolation(f"protected path changed: {path}")
        candidate = root / path
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(root_real):
            raise PolicyViolation(f"symlink path escape: {path}")
        parent = candidate.parent
        while parent != root and parent.is_relative_to(root):
            if parent.is_symlink() and not parent.resolve().is_relative_to(root_real):
                raise PolicyViolation(f"symlink path escape: {path}")
            parent = parent.parent
        validated.append(path)
    return tuple(validated)


_GIT_DENIED = {"commit", "push", "tag"}
_COMMAND_WRAPPERS = {"bash", "dash", "fish", "sh", "xargs", "zsh"}
_INTERPRETER_CODE_FLAGS = {
    "node": {"-e", "--eval"},
    "perl": {"-e"},
    "ruby": {"-e"},
}


def _git_subcommand(arguments: Sequence[str]) -> str | None:
    """Return the Git subcommand while skipping supported global options."""

    index = 0
    options_with_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            index += 1
            break
        if argument in options_with_value:
            index += 2
            continue
        if any(
            argument.startswith(prefix)
            for prefix in ("--git-dir=", "--work-tree=", "--namespace=")
        ):
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument.lower()
    return arguments[index].lower() if index < len(arguments) else None


def validate_command(argv: Sequence[str]) -> None:
    """Reject commands forbidden even when requested by a TaskContract."""

    if not argv or any("\x00" in item for item in argv):
        raise PolicyViolation("command argv must be non-empty and contain no NUL")
    executable = Path(argv[0]).name.lower()
    lowered = [part.lower() for part in argv]
    if executable in _COMMAND_WRAPPERS:
        raise PolicyViolation(f"command wrapper is forbidden: {executable}")
    if executable.startswith("python") and "-c" in lowered[1:]:
        raise PolicyViolation("inline interpreter code is forbidden: python -c")
    if _INTERPRETER_CODE_FLAGS.get(executable, set()).intersection(lowered[1:]):
        raise PolicyViolation(f"inline interpreter code is forbidden: {executable}")
    git_subcommand = _git_subcommand(argv[1:]) if executable == "git" else None
    if git_subcommand in _GIT_DENIED:
        raise PolicyViolation(f"forbidden git command: {git_subcommand}")
    if executable in {"kaggle", "kaggle.exe"} and "submit" in lowered[1:]:
        raise PolicyViolation("Kaggle submission commands are forbidden")
    if executable == "orchestrate.py" or any(
        Path(part).as_posix().endswith("scripts/orchestrate.py") for part in argv[1:]
    ):
        raise PolicyViolation("recursive orchestrator invocation is forbidden")
    secret_terms = {"kaggle.json", ".env", "printenv", "env"}
    if executable in secret_terms or any(part in {"kaggle.json", ".env"} for part in lowered):
        raise PolicyViolation("secret retrieval or display command is forbidden")
