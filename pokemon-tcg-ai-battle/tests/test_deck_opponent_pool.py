from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


MODULE = (
    Path(__file__).parents[1]
    / "scripts"
    / "policy_learning"
    / "build_deck_opponent_pool.py"
)
spec = importlib.util.spec_from_file_location("build_deck_opponent_pool", MODULE)
pool = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(pool)


def _write_deck(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(str(offset + index) for index in range(60)) + "\n",
        encoding="utf-8",
    )


def test_remote_rows_include_agent_tip_and_nested_dev_opponents(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    _write_deck(repo / "deck.csv", 1)
    _write_deck(repo / "opponents" / "nested" / "deck.csv", 101)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/agents/test", head],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/dev", head],
        cwd=repo,
        check=True,
    )

    rows = pool.remote_rows(repo)
    by_source = {row["source_id"]: row for row in rows}

    assert "origin/agents/test" in by_source
    assert "origin/dev" in by_source
    nested = "origin/dev:opponents/nested/deck.csv"
    assert nested in by_source
    assert by_source[nested]["source_path"] == "opponents/nested/deck.csv"
    assert all(row["source_commit"] == head for row in rows)
