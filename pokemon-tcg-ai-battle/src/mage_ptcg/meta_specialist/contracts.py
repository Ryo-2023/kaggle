from __future__ import annotations

from pathlib import Path

from mage_ptcg.continuous_league.contracts import publish_content_addressed_json


BUNDLE_SIZE_LIMIT_KIB = 202_400
BUNDLE_SIZE_LIMIT_BYTES = BUNDLE_SIZE_LIMIT_KIB * 1024


def ladder_mechanics_payload(*, checked_at_utc: str) -> dict[str, object]:
    """Return the versioned official ladder mechanics contract."""
    if not checked_at_utc.endswith("Z"):
        raise ValueError("checked_at_utc must be an explicit UTC timestamp ending in Z")
    return {
        "schema_version": "ladder-mechanics-v1",
        "checked_at_utc": checked_at_utc,
        "official_source": "https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/overview/description",
        "archive_format": ".tar.gz",
        "required_top_level_files": ["main.py", "deck.csv"],
        "bundle_size_limit_kib": BUNDLE_SIZE_LIMIT_KIB,
        "bundle_size_limit_bytes": BUNDLE_SIZE_LIMIT_BYTES,
        "max_daily_submissions": 5,
        "active_submission_limit": 2,
        "final_selection_limit": 2,
        "initial_mu": 600.0,
        "cpu_limit_percent": 200,
        "ram_limit_kib": 12_815_744,
        "agent_disk_limit_kib": 12_388_608,
        "agent_root": "/kaggle_simulations/agent/",
        "gpu_required": False,
        "network_required": False,
        "deadline_utc": "2026-08-16T23:59:00Z",
        "deadline_jst": "2026-08-17T08:59:00+09:00",
        "target_safe_upload_at_utc": "2026-08-15T23:59:00Z",
    }


def publish_ladder_mechanics(
    root: Path, *, checked_at_utc: str
) -> tuple[str, Path]:
    """Publish the checked official mechanics as an immutable manifest."""
    return publish_content_addressed_json(
        root,
        domain="meta-specialist-ladder-mechanics-v1",
        payload=ladder_mechanics_payload(checked_at_utc=checked_at_utc),
        id_field="ladder_mechanics_id",
    )
