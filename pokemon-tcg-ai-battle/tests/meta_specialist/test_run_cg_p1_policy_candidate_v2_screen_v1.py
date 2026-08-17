from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_v2_screen_adapter_is_sealed_to_v2_variants() -> None:
    from scripts.run_cg_p1_policy_candidate_v2_screen_v1 import VARIANT_IDS

    assert VARIANT_IDS == (
        "cg-p1-search-priority-v3",
        "cg-p1-gust-ko-v3",
        "cg-p1-carmine-tempo-v1",
        "cg-p1-carmine-tempo-v2",
    )


def test_v2_screen_adapter_cli_resolves_repo_scripts_package() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/run_cg_p1_policy_candidate_v2_screen_v1.py"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "cg-p1-carmine-tempo-v1" in result.stdout
    assert "--games-per-opponent-seat" in result.stdout


def test_confirmation_budget_accepts_eight_games_per_opponent_seat() -> None:
    from scripts.run_cg_p1_variant_screen_v1 import _validate_screen_budget

    assert _validate_screen_budget(workers=12, worker_recycle_games=64, games_per_opponent_seat=8) is None
