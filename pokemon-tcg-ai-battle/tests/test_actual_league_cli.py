"""Official cabt League adapter contracts without a real environment."""

from __future__ import annotations

from pathlib import Path

from scripts.run_actual_league import LeagueCapabilityError, run_official_league


def _ready_report() -> dict[str, object]:
    return {"status": "READY", "reason_code": "READY", "kaggle_environments_version": "1.32.0"}


def test_official_adapter_side_swaps_and_redacts_runner_result(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []

    def runner(**kwargs):
        seen.append(kwargs)
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.25, "raw_observation": {"hand": [{"id": 1}]}}

    result = run_official_league(
        champion_agent="rule",
        challenger_agent="deterministic",
        games=2,
        base_seed=3,
        output_path=tmp_path / "league.json",
        capability_report=_ready_report(),
        match_runner=runner,
    )

    assert [call["agent_a_name"] for call in seen] == ["rule", "deterministic"]
    assert result["wins"] == result["losses"] == 1
    assert result["actual_provenance"]["source"] == "official-cabt"
    assert "raw_observation" not in (tmp_path / "league.json").read_text(encoding="utf-8")


def test_official_adapter_refuses_unavailable_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "league.json"
    try:
        run_official_league(
            champion_agent="rule",
            challenger_agent="deterministic",
            games=2,
            base_seed=0,
            output_path=output,
            capability_report={"status": "UNAVAILABLE", "reason_code": "PLUGIN_NOT_REGISTERED"},
        )
    except LeagueCapabilityError as exc:
        assert "PLUGIN_NOT_REGISTERED" in str(exc)
    else:
        raise AssertionError("unavailable cabt must fail closed")
    assert not output.exists()
