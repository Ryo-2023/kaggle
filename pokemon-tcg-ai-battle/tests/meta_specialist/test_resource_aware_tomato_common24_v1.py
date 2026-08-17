from __future__ import annotations

import pytest


def test_common24_guardrail_is_bound_to_ae_candidate() -> None:
    from scripts.run_resource_aware_tomato_common24_v1 import (
        AE_CANDIDATE_ID,
        COMMON24_GAMES_PER_ARM,
        TomatoCommon24Error,
        validate_candidate_identity,
    )

    assert AE_CANDIDATE_ID.startswith("ae3075c2e096")
    assert COMMON24_GAMES_PER_ARM == 96
    with pytest.raises(TomatoCommon24Error, match="ae3075"):
        validate_candidate_identity("4d4aa8fd7642")


def test_common24_guardrail_rejects_non_24_schedule() -> None:
    from scripts.run_resource_aware_tomato_common24_v1 import (
        TomatoCommon24Error,
        validate_reference_ids,
    )

    with pytest.raises(TomatoCommon24Error, match="24"):
        validate_reference_ids(("one",))

