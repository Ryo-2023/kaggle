from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_self_owned_cg_deck_v1 import run_generation_v1
import scripts.run_self_owned_cg_deck_screen_v1 as screen


ROOT = Path(__file__).resolve().parents[2]
CARD_DB = ROOT / "data/raw/EN_Card_Data.csv"
SPEC = ROOT / "configs/meta_specialist/self_owned_cg_deck_spec_v1.json"
SOURCE_PACKAGE = ROOT / "runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1"
POOL_ROOT = ROOT / "opponents"


def _candidate(tmp_path: Path) -> Path:
    result = run_generation_v1(
        output=tmp_path / "candidate",
        card_db=CARD_DB,
        spec=SPEC,
        source_package=SOURCE_PACKAGE,
        public_scan_roots=(),
        seed=20260816,
        ordinal=11,
    )
    assert result["status"] == "COMPLETE"
    return tmp_path / "candidate/package"


def test_screen_requires_explicit_execute(tmp_path, capsys):
    candidate = _candidate(tmp_path)
    status = screen.main(
        [
            "--candidate-package",
            str(candidate),
            "--control-package",
            str(candidate),
            "--pool-root",
            str(POOL_ROOT),
            "--opponent-id",
            "aristophanivan_multiply",
            "--output",
            str(tmp_path / "screen"),
        ]
    )
    assert status == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_EXECUTE_REQUIRED"


def test_screen_binds_same_strata_and_aggregates(monkeypatch, tmp_path):
    candidate = _candidate(tmp_path)
    observed = {}

    def fake_evaluator(games, *, output_dir, max_workers, worker_recycle_games, overwrite):
        observed["games"] = tuple(games)
        rows = [
            {
                "outcome": "win" if game.metadata["arm_id"] == "self_owned_candidate_v1" else "loss",
                "seat": game.seat,
                "metadata": dict(game.metadata),
            }
            for game in games
        ]
        return {"rows": rows, "summary": {"completed_games": len(rows), "faults": 0}}

    monkeypatch.setattr(screen, "run_parallel_cabt_evaluation", fake_evaluator)
    result = screen.run_screen_v1(
        candidate_package=candidate,
        control_package=candidate,
        output=tmp_path / "screen",
        pool_root=POOL_ROOT,
        refs=("aristophanivan_multiply",),
        base_seed=991,
        games_per_opponent_seat=1,
        workers=1,
        worker_recycle_games=1,
        execute=True,
    )
    assert result["status"] == "COMPLETE"
    assert result["summary"]["candidate_delta_points"] == 100.0
    games = observed["games"]
    assert len(games) == 4
    candidate_games = [game for game in games if game.metadata["arm_id"] == "self_owned_candidate_v1"]
    control_games = [game for game in games if game.metadata["arm_id"] == "self_owned_control_v1"]
    assert {(game.opponent_id, game.seat, game.seed) for game in candidate_games} == {
        (game.opponent_id, game.seat, game.seed) for game in control_games
    }
    assert {game.metadata["deck_sha256"] for game in candidate_games} == {
        game.deck_sha256 for game in candidate_games
    }
    manifest = json.loads((tmp_path / "screen/manifest.json").read_text(encoding="utf-8"))
    assert manifest["research_only"] is True
    assert manifest["authority"]["submission_allowed"] is False


def test_screen_refuses_existing_output(tmp_path):
    candidate = _candidate(tmp_path)
    output = tmp_path / "screen"
    output.mkdir()
    (output / "sentinel").write_text("keep", encoding="utf-8")
    try:
        screen.run_screen_v1(
            candidate_package=candidate,
            control_package=candidate,
            output=output,
            pool_root=POOL_ROOT,
            refs=("aristophanivan_multiply",),
            execute=True,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing screen output must fail closed")
    assert (output / "sentinel").read_text(encoding="utf-8") == "keep"
