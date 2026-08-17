from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mine_team_knowledge", ROOT / "scripts" / "mine_team_knowledge.py"
)
assert SPEC is not None and SPEC.loader is not None
MINER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MINER
SPEC.loader.exec_module(MINER)


def test_parse_deck_preserves_exact_counts() -> None:
    assert MINER.parse_deck("5\n5\n741\n") == [
        {"card_id": 5, "count": 2},
        {"card_id": 741, "count": 1},
    ]
    assert MINER.parse_deck("not-an-id\n") is None


def test_generated_artifacts_pass_integrity_checks() -> None:
    output = ROOT / "artifacts" / "team-knowledge-mining"
    validation = MINER.validate_outputs(output)
    assert all(validation.values()), validation
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["validation"]["deterministic_output"] is True
