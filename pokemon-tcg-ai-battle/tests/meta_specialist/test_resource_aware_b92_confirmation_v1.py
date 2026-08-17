from __future__ import annotations

from pathlib import Path

import pytest


def test_confirmation_schedule_is_exactly_common24_eight_repetitions() -> None:
    from scripts.run_resource_aware_b92_confirmation_v1 import (
        CONFIRMATION_GAMES_PER_OPPONENT_SEAT,
        COMMON24_COUNT,
        expected_confirmation_games,
    )

    assert CONFIRMATION_GAMES_PER_OPPONENT_SEAT == 8
    assert COMMON24_COUNT == 24
    assert expected_confirmation_games() == 2 * 24 * 2 * 8


def test_confirmation_rejects_non_b92_candidate() -> None:
    from scripts.run_resource_aware_b92_confirmation_v1 import (
        B92_CANDIDATE_ID,
        ConfirmationError,
        assert_b92_identity,
    )

    with pytest.raises(ConfirmationError, match="b92"):
        assert_b92_identity("not-b92", "0" * 64)
    assert B92_CANDIDATE_ID.startswith("b92a3b55c5fa")


def test_confirmation_output_must_be_fresh_repo_child(tmp_path: Path) -> None:
    from scripts.run_resource_aware_b92_confirmation_v1 import (
        ConfirmationError,
        validate_output_root,
    )

    with pytest.raises(ConfirmationError, match="repo"):
        validate_output_root(Path("/tmp/not-repo"))
