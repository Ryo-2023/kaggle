"""Tests for §19 Worktree Guard."""

import pytest
from mage_ptcg.meta_specialist.worktree_guard_v1 import (
    WorktreeGuardV1Error,
    assert_worktree_clean_for_destructive_operation_v1,
    inspect_worktree_status_v1,
)


def test_inspect_worktree_status_runs():
    status = inspect_worktree_status_v1()
    assert isinstance(status.is_dirty, bool)
    assert status.untracked_count >= 0
    assert status.modified_count >= 0


def test_assert_worktree_clean_handles_non_git_repo(tmp_path):
    with pytest.raises(WorktreeGuardV1Error):
        assert_worktree_clean_for_destructive_operation_v1(str(tmp_path))
