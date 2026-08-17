from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from scripts import run_historical_meta_smoke_v1 as smoke


@dataclass(frozen=True)
class _FakeGame:
    timeout_seconds: float = 600.0


def test_smoke_can_bind_training_only_reference_subset(tmp_path: Path, monkeypatch) -> None:
    pool_root = tmp_path / "pool"
    pool_root.mkdir()
    (pool_root / "pool_manifest.json").write_text(
        json.dumps([{"id": "train-a"}, {"id": "dev-b"}]) + "\n",
        encoding="utf-8",
    )
    package = tmp_path / "package"
    package.mkdir()
    (package / "main.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    (package / "deck.csv").write_text("1\n", encoding="utf-8")
    seen: list[tuple[str, ...]] = []

    def fake_build_games(**kwargs):
        seen.append(tuple(kwargs["refs"]))
        return (_FakeGame(),)

    monkeypatch.setattr(smoke.arena, "_build_games", fake_build_games)
    monkeypatch.setattr(
        smoke,
        "run_parallel_cabt_evaluation",
        lambda games, **kwargs: {
            "rows": [{"outcome": "win"}],
            "summary": {"status": "COMPLETE", "requested_games": len(games)},
        },
    )

    summary = smoke.run_smoke(
        pool_root=pool_root,
        candidate_package=package,
        output_root=tmp_path / "out",
        base_seed=7,
        games_per_opponent_seat=1,
        workers=1,
        timeout_seconds=5.0,
        reference_ids=["train-a"],
    )

    assert seen == [("train-a",)]
    assert summary["reference_ids"] == ["train-a"]
    assert summary["status"] == "COMPLETE"

