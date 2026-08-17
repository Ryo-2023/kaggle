"""Meta-specialist ladder-mechanics contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.contracts import LeagueContractError
from mage_ptcg.meta_specialist.contracts import (
    ladder_mechanics_payload,
    publish_ladder_mechanics,
)


def test_ladder_mechanics_v1_has_exact_official_limits() -> None:
    """Catches incorrect official bundle, resource, or deadline limits."""
    payload = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")

    assert payload["schema_version"] == "ladder-mechanics-v1"
    assert payload["archive_format"] == ".tar.gz"
    assert payload["required_top_level_files"] == ["main.py", "deck.csv"]
    assert payload["bundle_size_limit_kib"] == 202_400
    assert payload["bundle_size_limit_bytes"] == 207_257_600
    assert payload["max_daily_submissions"] == 5
    assert payload["active_submission_limit"] == 2
    assert payload["final_selection_limit"] == 2
    assert payload["initial_mu"] == 600.0
    assert payload["cpu_limit_percent"] == 200
    assert payload["ram_limit_kib"] == 12_815_744
    assert payload["agent_disk_limit_kib"] == 12_388_608
    assert payload["agent_root"] == "/kaggle_simulations/agent/"
    assert payload["gpu_required"] is False
    assert payload["network_required"] is False
    assert payload["deadline_utc"] == "2026-08-16T23:59:00Z"
    assert payload["deadline_jst"] == "2026-08-17T08:59:00+09:00"
    assert payload["target_safe_upload_at_utc"] == "2026-08-15T23:59:00Z"


@pytest.mark.parametrize("checked_at_utc", ["2026-08-01T00:00:00+09:00", ""])
def test_ladder_mechanics_requires_explicit_utc_checked_timestamp(
    checked_at_utc: str,
) -> None:
    """Catches a mechanics snapshot whose verification time is not explicit UTC."""
    with pytest.raises(ValueError, match="explicit UTC timestamp ending in Z"):
        ladder_mechanics_payload(checked_at_utc=checked_at_utc)


def test_ladder_mechanics_publisher_is_idempotent_and_rejects_collisions(
    tmp_path: Path,
) -> None:
    """Catches mutable or silently overwritten content-addressed manifests."""
    first_id, first_path = publish_ladder_mechanics(
        tmp_path,
        checked_at_utc="2026-08-01T00:00:00Z",
    )
    second_id, second_path = publish_ladder_mechanics(
        tmp_path,
        checked_at_utc="2026-08-01T00:00:00Z",
    )

    assert second_id == first_id
    assert second_path == first_path
    assert json.loads(first_path.read_text(encoding="utf-8"))["ladder_mechanics_id"] == first_id

    first_path.write_text(
        json.dumps({"ladder_mechanics_id": first_id, "corrupted": True}),
        encoding="utf-8",
    )
    with pytest.raises(LeagueContractError, match="content-address collision"):
        publish_ladder_mechanics(
            tmp_path,
            checked_at_utc="2026-08-01T00:00:00Z",
        )
