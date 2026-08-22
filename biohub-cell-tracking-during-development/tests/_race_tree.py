"""Locate the persisted detector-fixed race artifacts from any checkout.

Three reproducibility test modules need the same read-only artifact tree.  Each
originally hardcoded ``Path(__file__).resolve().parents[4]``, which encodes the
directory depth of the worktree the test was written in.  Run from a worktree at
a different depth — the Codex mainline checkout, for instance — that index lands
one level off and every dependent test skips *silently*.  A guard that quietly
stops running is worse than no guard, so the lookup lives here once and searches
instead of counting.

Resolution order:

1. ``BIOHUB_DETECTOR_FIXED_RACE_ROOT`` if set (explicit override always wins).
2. This checkout's own ``artifacts/detector_fixed_race`` — correct when the tests
   run inside the worktree that produced the runs.
3. A sibling ``scratch/strong-baseline-v1`` checkout, found by walking upwards —
   correct when the tests run from another worktree of the same repository.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_OVERRIDE = "BIOHUB_DETECTOR_FIXED_RACE_ROOT"
_CHECKOUT = Path(__file__).resolve().parents[1]
_SIBLING_SUFFIX = Path("scratch/strong-baseline-v1/biohub-cell-tracking-during-development")


def candidate_race_trees() -> tuple[Path, ...]:
    """Every place the race tree could legitimately live, most specific first."""

    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return (Path(override),)

    candidates = [_CHECKOUT / "artifacts" / "detector_fixed_race"]
    for ancestor in (_CHECKOUT, *_CHECKOUT.parents):
        sibling = ancestor / _SIBLING_SUFFIX / "artifacts" / "detector_fixed_race"
        if sibling.is_dir():
            candidates.append(sibling)
            break
    return tuple(candidates)


def find_race_tree() -> Path | None:
    """Return the first reachable race tree, or ``None`` when there is none."""

    for candidate in candidate_race_trees():
        if candidate.is_dir():
            return candidate
    return None


def unreachable_message() -> str:
    """Explain *where* we looked, so a skip is diagnosable rather than mysterious."""

    searched = ", ".join(str(path) for path in candidate_race_trees())
    return f"detector-fixed race artifacts are not reachable; searched: {searched}"
